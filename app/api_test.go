package app

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/labstack/echo/v4"
)

const testSecret = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

func newExportTrackContext(activityId, bearerToken string) (echo.Context, *httptest.ResponseRecorder) {
	return newActivityContext(http.MethodGet, "/api/activity/"+activityId+"/export", activityId, bearerToken)
}

func TestHandleExportTrack_MissingAuthHeader(t *testing.T) {
	c, _ := newExportTrackContext("12345", "")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handleExportTrack(c)

	assertHTTPErrorCode(t, err, http.StatusUnauthorized)
}

func TestHandleExportTrack_InvalidAuthFormat(t *testing.T) {
	c, _ := newExportTrackContext("12345", "")
	c.Request().Header.Set("Authorization", "Token not-bearer-format")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handleExportTrack(c)

	assertHTTPErrorCode(t, err, http.StatusUnauthorized)
}

func TestHandleExportTrack_InvalidJWT(t *testing.T) {
	c, _ := newExportTrackContext("12345", "invalid.jwt.token")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handleExportTrack(c)

	assertHTTPErrorCode(t, err, http.StatusForbidden, http.StatusUnauthorized)
}

func TestHandleExportTrack_ExpiredJWT(t *testing.T) {
	expiredToken, _, err := GenerateJWT(12345, testSecret, -1*time.Hour)
	if err != nil {
		t.Fatalf("failed to generate expired token: %v", err)
	}
	c, _ := newExportTrackContext("12345", expiredToken)

	s := &ServerState{config: Config{Secret: testSecret}}
	err = s.handleExportTrack(c)

	assertHTTPErrorCode(t, err, http.StatusForbidden, http.StatusUnauthorized)
}

func TestHandleExportTrack_WrongSecret(t *testing.T) {
	// Token signed with a different secret should be rejected.
	token, _, err := GenerateJWT(12345, testSecret, 1*time.Hour)
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}
	c, _ := newExportTrackContext("12345", token)

	wrongSecret := "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
	s := &ServerState{config: Config{Secret: wrongSecret}}
	err = s.handleExportTrack(c)

	assertHTTPErrorCode(t, err, http.StatusForbidden, http.StatusUnauthorized)
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

// newActivityContext builds an echo context for an /api/activity/:activityId/*
// route with the path param populated, optionally attaching a Bearer token.
func newActivityContext(method, path, activityId, bearerToken string) (echo.Context, *httptest.ResponseRecorder) {
	e := echo.New()
	req := httptest.NewRequest(method, path, nil)
	if bearerToken != "" {
		req.Header.Set("Authorization", "Bearer "+bearerToken)
	}
	rec := httptest.NewRecorder()
	c := e.NewContext(req, rec)
	c.SetParamNames("activityId")
	c.SetParamValues(activityId)
	return c, rec
}

// newPersistActivityContext builds an echo context for the
// POST /api/activity/:activityId/persist route.
func newPersistActivityContext(activityId, bearerToken string) (echo.Context, *httptest.ResponseRecorder) {
	return newActivityContext(http.MethodPost, "/api/activity/"+activityId+"/persist", activityId, bearerToken)
}

// assertHTTPErrorCode fails the test unless err is an *echo.HTTPError whose code
// is one of the wantCodes.
func assertHTTPErrorCode(t *testing.T, err error, wantCodes ...int) {
	t.Helper()
	if err == nil {
		t.Fatal("expected an error, got nil")
	}
	httpErr, ok := err.(*echo.HTTPError)
	if !ok {
		t.Fatalf("expected *echo.HTTPError, got %T", err)
	}
	for _, code := range wantCodes {
		if httpErr.Code == code {
			return
		}
	}
	t.Errorf("expected status in %v, got %d", wantCodes, httpErr.Code)
}

func TestHandlePersistActivity_MissingAuthHeader(t *testing.T) {
	c, _ := newPersistActivityContext("12345", "")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handlePersistActivity(c)

	assertHTTPErrorCode(t, err, http.StatusUnauthorized)
}

func TestHandlePersistActivity_InvalidAuthFormat(t *testing.T) {
	c, _ := newPersistActivityContext("12345", "")
	c.Request().Header.Set("Authorization", "Token not-bearer-format")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handlePersistActivity(c)

	assertHTTPErrorCode(t, err, http.StatusUnauthorized)
}

func TestHandlePersistActivity_InvalidJWT(t *testing.T) {
	c, _ := newPersistActivityContext("12345", "invalid.jwt.token")

	s := &ServerState{config: Config{Secret: testSecret}}
	err := s.handlePersistActivity(c)

	assertHTTPErrorCode(t, err, http.StatusForbidden, http.StatusUnauthorized)
}

func TestHandlePersistActivity_ExpiredJWT(t *testing.T) {
	expiredToken, _, err := GenerateJWT(12345, testSecret, -1*time.Hour)
	if err != nil {
		t.Fatalf("failed to generate expired token: %v", err)
	}
	c, _ := newPersistActivityContext("12345", expiredToken)

	s := &ServerState{config: Config{Secret: testSecret}}
	err = s.handlePersistActivity(c)

	assertHTTPErrorCode(t, err, http.StatusForbidden, http.StatusUnauthorized)
}

func TestHandlePersistActivity_WrongSecret(t *testing.T) {
	// Token signed with a different secret should be rejected.
	token, _, err := GenerateJWT(12345, testSecret, 1*time.Hour)
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}
	c, _ := newPersistActivityContext("12345", token)

	wrongSecret := "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
	s := &ServerState{config: Config{Secret: wrongSecret}}
	err = s.handlePersistActivity(c)

	assertHTTPErrorCode(t, err, http.StatusForbidden, http.StatusUnauthorized)
}

// TestHandlePersistActivity_SuccessfulPersist requires Redis for auth, a Strava
// token in the store, and blob storage credentials. Run as an integration test.
func TestHandlePersistActivity_SuccessfulPersist(t *testing.T) {
	t.Skip("requires Redis connection, Strava token, and blob storage — run as integration test")
}
