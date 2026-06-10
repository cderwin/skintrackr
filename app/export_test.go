package app

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/labstack/echo/v4"
)

const testSecret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

func newExportTrackContext(method, url string) (echo.Context, *httptest.ResponseRecorder) {
	e := echo.New()
	req := httptest.NewRequest(method, url, nil)
	rec := httptest.NewRecorder()
	return e.NewContext(req, rec), rec
}

func TestHandleExportTrack_MissingAuthHeader(t *testing.T) {
	c, _ := newExportTrackContext(http.MethodGet, "/api/export-track?activityId=12345")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handleExportTrack(c)

	if err == nil {
		t.Fatal("expected error for missing Authorization header")
	}
	httpErr, ok := err.(*echo.HTTPError)
	if !ok {
		t.Fatalf("expected *echo.HTTPError, got %T", err)
	}
	if httpErr.Code != http.StatusUnauthorized {
		t.Errorf("expected status %d, got %d", http.StatusUnauthorized, httpErr.Code)
	}
}

func TestHandleExportTrack_InvalidAuthFormat(t *testing.T) {
	e := echo.New()
	req := httptest.NewRequest(http.MethodGet, "/api/export-track?activityId=12345", nil)
	req.Header.Set("Authorization", "Token not-bearer-format")
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handleExportTrack(c)

	if err == nil {
		t.Fatal("expected error for invalid Authorization format")
	}
	httpErr, ok := err.(*echo.HTTPError)
	if !ok {
		t.Fatalf("expected *echo.HTTPError, got %T", err)
	}
	if httpErr.Code != http.StatusUnauthorized {
		t.Errorf("expected status %d, got %d", http.StatusUnauthorized, httpErr.Code)
	}
}

func TestHandleExportTrack_InvalidJWT(t *testing.T) {
	e := echo.New()
	req := httptest.NewRequest(http.MethodGet, "/api/export-track?activityId=12345", nil)
	req.Header.Set("Authorization", "Bearer invalid.jwt.token")
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handleExportTrack(c)

	if err == nil {
		t.Fatal("expected error for invalid JWT")
	}
	httpErr, ok := err.(*echo.HTTPError)
	if !ok {
		t.Fatalf("expected *echo.HTTPError, got %T", err)
	}
	// AuthenticateToken returns 403 for invalid/expired tokens
	if httpErr.Code != http.StatusForbidden && httpErr.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 or 403, got %d", httpErr.Code)
	}
}

func TestHandleExportTrack_ExpiredJWT(t *testing.T) {
	expiredToken, _, err := GenerateJWT(12345, testSecret, -1*time.Hour)
	if err != nil {
		t.Fatalf("failed to generate expired token: %v", err)
	}

	e := echo.New()
	req := httptest.NewRequest(http.MethodGet, "/api/export-track?activityId=12345", nil)
	req.Header.Set("Authorization", "Bearer "+expiredToken)
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)

	s := &ServerState{config: Config{Secret: testSecret}}
	err = s.handleExportTrack(c)

	if err == nil {
		t.Fatal("expected error for expired JWT")
	}
	httpErr, ok := err.(*echo.HTTPError)
	if !ok {
		t.Fatalf("expected *echo.HTTPError, got %T", err)
	}
	if httpErr.Code != http.StatusForbidden && httpErr.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 or 403, got %d", httpErr.Code)
	}
}

func TestHandleExportTrack_WrongSecret(t *testing.T) {
	// Token signed with a different secret should be rejected
	token, _, err := GenerateJWT(12345, testSecret, 1*time.Hour)
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}

	wrongSecret := "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
	e := echo.New()
	req := httptest.NewRequest(http.MethodGet, "/api/export-track?activityId=12345", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)

	s := &ServerState{config: Config{Secret: wrongSecret}}
	err = s.handleExportTrack(c)

	if err == nil {
		t.Fatal("expected error for token signed with wrong secret")
	}
	httpErr, ok := err.(*echo.HTTPError)
	if !ok {
		t.Fatalf("expected *echo.HTTPError, got %T", err)
	}
	if httpErr.Code != http.StatusForbidden && httpErr.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 or 403, got %d", httpErr.Code)
	}
}

// TestHandleExportTrack_MissingActivityId requires Redis for the JWT revocation
// check that precedes the activityId validation. Run as an integration test.
func TestHandleExportTrack_MissingActivityId(t *testing.T) {
	t.Skip("requires Redis connection for JWT revocation check — run as integration test")
}

// TestHandleExportTrack_SuccessfulExport requires Redis for auth and a Strava token
// in the store. Run as an integration test.
func TestHandleExportTrack_SuccessfulExport(t *testing.T) {
	t.Skip("requires Redis connection and Strava token in store — run as integration test")
}
