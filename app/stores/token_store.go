// Package stores implements the business logic for persisting auth tokens and
// Strava activities. It depends only on the clients package for storage and
// never imports the redis or AWS/S3 libraries directly.
package stores

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"time"

	"github.com/cderwin/skintrackr/app/clients"
	"github.com/cderwin/skintrackr/app/crypto"
)

type TokenInfo struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	ExpiresAt    int64  `json:"expires_at"`
}

// StravaTokenRefresher exchanges a refresh token for a fresh Strava token. It
// is satisfied by an adapter in the app package so the store need not perform
// (or even know about) Strava HTTP requests.
type StravaTokenRefresher interface {
	RefreshToken(refreshToken string) (TokenInfo, error)
}

// TokenStore manages Strava OAuth tokens, OAuth state, and JWT revocation
// tracking, backed by Redis.
type TokenStore struct {
	redis     *clients.RedisClient
	secret    string
	refresher StravaTokenRefresher
}

func NewTokenStore(redis *clients.RedisClient, secret string, refresher StravaTokenRefresher) *TokenStore {
	return &TokenStore{redis: redis, secret: secret, refresher: refresher}
}

func (s *TokenStore) FetchToken(athleteId int) (string, error) {
	tokenInfo, err := s.FetchTokenInfo(athleteId)
	if err != nil {
		return "", err
	}

	return tokenInfo.AccessToken, nil
}

func (s *TokenStore) SaveToken(athleteId int, token TokenInfo) error {
	authKey := fmt.Sprintf("athlete:%d:strava-token", athleteId)

	encryptedAccessToken, err := crypto.Encrypt(token.AccessToken, s.secret)
	if err != nil {
		return fmt.Errorf("failed to encrypt access token: %w", err)
	}

	encryptedRefreshToken, err := crypto.Encrypt(token.RefreshToken, s.secret)
	if err != nil {
		return fmt.Errorf("failed to encrypt refresh token: %w", err)
	}

	err = s.redis.HSet(authKey, map[string]string{
		"access_token":  encryptedAccessToken,
		"refresh_token": encryptedRefreshToken,
		"expires_at":    strconv.FormatInt(token.ExpiresAt, 10),
	})
	if err != nil {
		slog.Error("error saving token", "err", err)
		return err
	}

	slog.Info("saved new token", "athlete_id", athleteId)
	return nil
}

func (s *TokenStore) FetchTokenInfo(athleteId int) (*TokenInfo, error) {
	authKey := fmt.Sprintf("athlete:%d:strava-token", athleteId)
	fields, err := s.redis.HGetAll(authKey)
	if err != nil {
		if errors.Is(err, clients.ErrNotFound) {
			slog.Error("fetch token error: athlete not found", "athlete_id", athleteId)
			return nil, err
		}

		slog.Error("fetch token error: redis request failed", "err", err)
		return nil, err
	}

	expiresAt, err := strconv.ParseInt(fields["expires_at"], 10, 64)
	if err != nil {
		return nil, fmt.Errorf("failed to parse expires_at: %w", err)
	}

	tokenInfo := TokenInfo{ExpiresAt: expiresAt}

	tokenInfo.AccessToken, err = crypto.Decrypt(fields["access_token"], s.secret)
	if err != nil {
		return nil, fmt.Errorf("failed to decrypt access token: %w", err)
	}

	tokenInfo.RefreshToken, err = crypto.Decrypt(fields["refresh_token"], s.secret)
	if err != nil {
		return nil, fmt.Errorf("failed to decrypt refresh token: %w", err)
	}

	if tokenInfo.ExpiresAt < time.Now().Unix() {
		slog.Info("token expired, refreshing token", "athlete_id", athleteId)
		return s.refreshToken(athleteId, tokenInfo)
	}

	return &tokenInfo, nil
}

func (s *TokenStore) refreshToken(athleteId int, token TokenInfo) (*TokenInfo, error) {
	newToken, err := s.refresher.RefreshToken(token.RefreshToken)
	if err != nil {
		slog.Error("error refreshing token", "err", err)
		return nil, err
	}

	s.SaveToken(athleteId, newToken)
	return &newToken, nil
}

// generateStateToken creates a random state token
func generateStateToken() string {
	bytes := make([]byte, 32)
	rand.Read(bytes)
	return hex.EncodeToString(bytes)
}

// SaveOAuthState stores a state token in Redis for CSRF protection
// Returns the state token
func (s *TokenStore) SaveOAuthState() (string, error) {
	state := generateStateToken()
	key := fmt.Sprintf("oauth:state:%s", state)

	// Store the state with a 10-minute expiration
	err := s.redis.Set(key, strconv.FormatInt(time.Now().Unix(), 10), 10*time.Minute)
	if err != nil {
		return "", fmt.Errorf("failed to save OAuth state: %w", err)
	}

	return state, nil
}

// GetOAuthState verifies and deletes a state token
func (s *TokenStore) GetOAuthState(state string) error {
	key := fmt.Sprintf("oauth:state:%s", state)

	// Get and delete the state in one operation
	_, err := s.redis.GetDel(key)
	if errors.Is(err, clients.ErrNotFound) {
		return fmt.Errorf("invalid or expired state token")
	}
	if err != nil {
		return fmt.Errorf("failed to retrieve OAuth state: %w", err)
	}

	return nil
}

// SaveJWTToken stores JWT metadata in Redis for revocation tracking
// The token is stored with a TTL matching its expiration time
func (s *TokenStore) SaveJWTToken(jti string, athleteID int, issuedAt time.Time, expiresAt time.Time) error {
	key := fmt.Sprintf("jwt:jti:%s", jti)

	// Calculate TTL based on expiration time
	ttl := time.Until(expiresAt)
	if ttl <= 0 {
		return fmt.Errorf("token already expired")
	}

	// Store token metadata
	err := s.redis.HSet(key, map[string]string{
		"athlete_id": strconv.Itoa(athleteID),
		"issued_at":  strconv.FormatInt(issuedAt.Unix(), 10),
		"expires_at": strconv.FormatInt(expiresAt.Unix(), 10),
	})
	if err != nil {
		return fmt.Errorf("failed to save JWT metadata: %w", err)
	}

	// Set expiration
	if err := s.redis.Expire(key, ttl); err != nil {
		return fmt.Errorf("failed to set JWT expiration: %w", err)
	}

	slog.Info("saved JWT token metadata", "jti", jti, "athlete_id", athleteID)
	return nil
}

// RevokeJWTToken marks a JWT token as revoked
// The revocation is stored until the token's expiration time
func (s *TokenStore) RevokeJWTToken(jti string) error {
	// First, check if the token exists
	jwtKey := fmt.Sprintf("jwt:jti:%s", jti)
	exists, err := s.redis.Exists(jwtKey)
	if err != nil {
		return fmt.Errorf("failed to check token existence: %w", err)
	}
	if !exists {
		return fmt.Errorf("token not found or already expired")
	}

	// Get the token's expiration time
	ttl, err := s.redis.TTL(jwtKey)
	if err != nil {
		return fmt.Errorf("failed to get token TTL: %w", err)
	}

	// Mark as revoked with the same TTL
	revokeKey := fmt.Sprintf("jwt:revoked:%s", jti)
	err = s.redis.Set(revokeKey, strconv.FormatInt(time.Now().Unix(), 10), ttl)
	if err != nil {
		return fmt.Errorf("failed to revoke token: %w", err)
	}

	slog.Info("revoked JWT token", "jti", jti)
	return nil
}

// IsJWTRevoked checks if a JWT token has been revoked
func (s *TokenStore) IsJWTRevoked(jti string) (bool, error) {
	revokeKey := fmt.Sprintf("jwt:revoked:%s", jti)

	exists, err := s.redis.Exists(revokeKey)
	if err != nil {
		return false, fmt.Errorf("failed to check revocation status: %w", err)
	}

	return exists, nil
}
