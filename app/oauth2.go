package app

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"

	"github.com/labstack/echo/v4"
)

type TokenResponse struct {
	TokenType    string `json:"token_type"`
	ExpiresAt    int64  `json:"expires_at"`
	ExpiresIn    int    `json:"expires_in"`
	RefreshToken string `json:"refresh_token"`
	AccessToken  string `json:"access_token"`
	Athlete      struct {
		ID       int    `json:"id"`
		Username string `json:"username"`
	} `json:"athlete"`
}

func (s *ServerState) handleConnect(c echo.Context) error {
	redirectUrl, err := url.JoinPath(s.config.BaseUrl, "oauth2/callback")
	if err != nil {
		return err
	}

	authorizationUrl, err := url.Parse(authUrl)
	params := authorizationUrl.Query()
	params.Add("client_id", s.config.StravaClientId)
	params.Add("redirect_uri", redirectUrl)
	params.Add("response_type", "code")
	params.Add("scope", "read,activity:read_all")
	authorizationUrl.RawQuery = params.Encode()

	c.Redirect(http.StatusFound, authorizationUrl.String())
	return nil
}

func (s *ServerState) handleCallback(c echo.Context) error {
	// Get the authorization code from query params
	code := c.QueryParam("code")
	if code == "" {
		return echo.NewHTTPError(http.StatusBadRequest, "No code in callback")
	}

	// Exchange code for access token
	token, err := exchangeCode(code, &s.config, &s.stravaClient)
	if err != nil {
		slog.Error("failed to exchange code with strava", "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "Failed to exchange temporary code with strava")
	}

	slog.Info("Token exchange completed for oauth2 callback", "athlete_id", token.Athlete.ID, "athlete_username", token.Athlete.Username)
	err = s.store.SaveToken(token.Athlete.ID, TokenInfo{AccessToken: token.AccessToken, RefreshToken: token.RefreshToken, ExpiresAt: int64(token.ExpiresAt)})
	if err != nil {
		slog.Error("failed to save token to redis", "athlete_id", token.Athlete.ID, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to save token to redis")
	}

	// Hand the token to the static confirmation page via the URL fragment,
	// which browsers never send back to the server
	return c.Redirect(http.StatusFound, confirmationUrl(token))
}

// confirmationUrl builds the confirmation page URL carrying the Strava token in its fragment
func confirmationUrl(token *TokenResponse) string {
	fragment := url.Values{
		"access_token": {token.AccessToken},
		"expires_at":   {strconv.FormatInt(token.ExpiresAt, 10)},
		"athlete_id":   {strconv.Itoa(token.Athlete.ID)},
	}
	return "/static/confirmation.html#" + fragment.Encode()
}

func exchangeCode(code string, config *Config, client *StravaClient) (*TokenResponse, error) {
	formData := map[string]string{
		"client_id":     config.StravaClientId,
		"client_secret": config.StravaClientSecret,
		"code":          code,
		"grant_type":    "authorization_code",
	}

	body, err := client.performRequestForm("POST", tokenUrl, formData)
	if err != nil {
		return nil, err
	}

	var token TokenResponse
	if err := json.NewDecoder(body).Decode(&token); err != nil {
		return nil, err
	}

	return &token, nil
}
