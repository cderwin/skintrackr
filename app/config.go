package app

import (
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"os"
)

type BlobStorageConfig struct {
	BucketName string
	AccessKeyId string
	SecretAccessKey string
	S3ApiEndpoint string
}

func LoadBlobStorageConfig() BlobStorageConfig {
	accessKeyId := os.Getenv("AWS_ACCESS_KEY_ID")
	if accessKeyId == "" {
		slog.Error("AWS_ACCESS_KEY_ID must be set")
		panic("invalid configuration")
	}

	secretAccessKey := os.Getenv("AWS_SECRET_ACCESS_KEY")
	if secretAccessKey == "" {
		slog.Error("AWS_SECRET_ACCESS_KEY must be set")
		panic("invalid configuration")
	}

	return BlobStorageConfig{
		BucketName: "skintrackr-storage",
		AccessKeyId: accessKeyId,
		SecretAccessKey: secretAccessKey,
		S3ApiEndpoint: "https://t3.storage.dev",
	}
}

type Config struct {
	BlobStorageConfig BlobStorageConfig
	BaseUrl            string
	StravaClientId     string
	StravaClientSecret string
	VerifyToken        string
	UpstashRedisUrl    string
	Secret             string
}

func randomString(byteLength int) string {
	bytes := make([]byte, byteLength)
	rand.Read(bytes)
	return hex.EncodeToString(bytes)
}

func LoadConfig() Config {
	baseUrl := os.Getenv("APP_BASE_URL")
	if baseUrl == "" {
		baseUrl = "http://localhost:8080"
	}

	secret := os.Getenv("APP_SECRET")
	if secret == "" {
		slog.Error("APP_SECRET must be set")
		panic("invalid configuration")
	}

	clientId := os.Getenv("STRAVA_CLIENT_ID")
	clientSecret := os.Getenv("STRAVA_CLIENT_SECRET")
	if clientId == "" || clientSecret == "" {
		slog.Error("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET must be set")
		panic("invalid configuration")
	}

	upstashRedisUrl := os.Getenv("UPSTASH_REDIS_URL")
	if upstashRedisUrl == "" {
		slog.Error("UPSTASH_REDIS_URL environment variable must be set")
		panic("invalid configuration")
	}
	return Config{
		BlobStorageConfig: LoadBlobStorageConfig(),
		BaseUrl:            baseUrl,
		StravaClientId:     clientId,
		StravaClientSecret: clientSecret,
		VerifyToken:        randomString(16),
		UpstashRedisUrl:    upstashRedisUrl,
		Secret:             secret,
	}
}
