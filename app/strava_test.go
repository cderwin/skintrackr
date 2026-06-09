package app

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestStravaClient_performRequest(t *testing.T) {
	tests := []struct {
		name           string
		token          string
		method         string
		responseStatus int
		responseBody   string
		expectError    bool
	}{
		{
			name:           "successful GET request with token",
			token:          "test-token-123",
			method:         "GET",
			responseStatus: http.StatusOK,
			responseBody:   `{"id": 123}`,
			expectError:    false,
		},
		{
			name:           "successful GET request without token",
			token:          "",
			method:         "GET",
			responseStatus: http.StatusOK,
			responseBody:   `{"success": true}`,
			expectError:    false,
		},
		{
			name:           "failed request with 4xx status",
			token:          "test-token-123",
			method:         "GET",
			responseStatus: http.StatusBadRequest,
			responseBody:   `{"error": "bad request"}`,
			expectError:    true,
		},
		{
			name:           "failed request with 5xx status",
			token:          "test-token-123",
			method:         "GET",
			responseStatus: http.StatusInternalServerError,
			responseBody:   `{"error": "server error"}`,
			expectError:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Create a test server
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Verify the Authorization header if token is provided
				if tt.token != "" {
					authHeader := r.Header.Get("Authorization")
					expectedAuth := "Bearer " + tt.token
					if authHeader != expectedAuth {
						t.Errorf("expected Authorization header %q, got %q", expectedAuth, authHeader)
					}
				}

				// Verify the method
				if r.Method != tt.method {
					t.Errorf("expected method %s, got %s", tt.method, r.Method)
				}

				w.WriteHeader(tt.responseStatus)
				w.Write([]byte(tt.responseBody))
			}))
			defer server.Close()

			// Create client and make request
			client := NewStravaClient(tt.token)
			body, err := client.performRequest(tt.method, server.URL, nil)

			// Check error expectation
			if tt.expectError && err == nil {
				t.Error("expected error but got none")
			}
			if !tt.expectError && err != nil {
				t.Errorf("unexpected error: %v", err)
			}

			// Verify response body if no error expected
			if !tt.expectError && body != nil {
				responseBytes, _ := io.ReadAll(body)
				if string(responseBytes) != tt.responseBody {
					t.Errorf("expected body %q, got %q", tt.responseBody, string(responseBytes))
				}
			}
		})
	}
}

func TestStravaClient_performRequestWithHeaders(t *testing.T) {
	tests := []struct {
		name            string
		token           string
		customHeaders   map[string]string
		expectedHeaders map[string]string
	}{
		{
			name:  "request with custom headers and token",
			token: "test-token",
			customHeaders: map[string]string{
				"Content-Type": "application/json",
				"X-Custom":     "custom-value",
			},
			expectedHeaders: map[string]string{
				"Authorization": "Bearer test-token",
				"Content-Type":  "application/json",
				"X-Custom":      "custom-value",
			},
		},
		{
			name:  "request with custom headers without token",
			token: "",
			customHeaders: map[string]string{
				"Content-Type": "application/x-www-form-urlencoded",
			},
			expectedHeaders: map[string]string{
				"Content-Type": "application/x-www-form-urlencoded",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Verify all expected headers are present
				for key, expectedValue := range tt.expectedHeaders {
					actualValue := r.Header.Get(key)
					if actualValue != expectedValue {
						t.Errorf("expected header %s=%q, got %q", key, expectedValue, actualValue)
					}
				}
				w.WriteHeader(http.StatusOK)
				w.Write([]byte("OK"))
			}))
			defer server.Close()

			client := NewStravaClient(tt.token)
			_, err := client.performRequestWithHeaders("GET", server.URL, nil, tt.customHeaders)

			if err != nil {
				t.Errorf("unexpected error: %v", err)
			}
		})
	}
}

func TestStravaClient_performRequestForm(t *testing.T) {
	tests := []struct {
		name         string
		formData     map[string]string
		expectedBody map[string]string
	}{
		{
			name: "simple form data",
			formData: map[string]string{
				"client_id":     "12345",
				"client_secret": "secret",
				"grant_type":    "authorization_code",
			},
			expectedBody: map[string]string{
				"client_id":     "12345",
				"client_secret": "secret",
				"grant_type":    "authorization_code",
			},
		},
		{
			name: "form data with special characters",
			formData: map[string]string{
				"code":         "abc123!@#",
				"redirect_uri": "https://example.com/callback?foo=bar",
			},
			expectedBody: map[string]string{
				"code":         "abc123!@#",
				"redirect_uri": "https://example.com/callback?foo=bar",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Verify Content-Type header
				contentType := r.Header.Get("Content-Type")
				if contentType != "application/x-www-form-urlencoded" {
					t.Errorf("expected Content-Type application/x-www-form-urlencoded, got %q", contentType)
				}

				// Parse form data
				if err := r.ParseForm(); err != nil {
					t.Fatalf("failed to parse form: %v", err)
				}

				// Verify all form fields
				for key, expectedValue := range tt.expectedBody {
					actualValue := r.FormValue(key)
					if actualValue != expectedValue {
						t.Errorf("expected form field %s=%q, got %q", key, expectedValue, actualValue)
					}
				}

				w.WriteHeader(http.StatusOK)
				w.Write([]byte(`{"success": true}`))
			}))
			defer server.Close()

			client := NewStravaClient("")
			body, err := client.performRequestForm("POST", server.URL, tt.formData)

			if err != nil {
				t.Errorf("unexpected error: %v", err)
			}

			// Verify we can read the response
			if body != nil {
				responseBytes, _ := io.ReadAll(body)
				if !strings.Contains(string(responseBytes), "success") {
					t.Errorf("unexpected response body: %s", string(responseBytes))
				}
			}
		})
	}
}

func TestStravaClient_GetActivity(t *testing.T) {
	tests := []struct {
		name           string
		activityID     string
		responseStatus int
		responseBody   string
		expectError    bool
		expectedName   string
	}{
		{
			name:           "successful activity fetch",
			activityID:     "12345",
			responseStatus: http.StatusOK,
			responseBody:   `{"id": 12345, "name": "Morning Run", "distance": 5000}`,
			expectError:    false,
			expectedName:   "Morning Run",
		},
		{
			name:           "activity not found",
			activityID:     "99999",
			responseStatus: http.StatusNotFound,
			responseBody:   `{"error": "not found"}`,
			expectError:    true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				// Verify the URL contains the activity ID
				if !strings.Contains(r.URL.Path, tt.activityID) {
					t.Errorf("expected URL to contain activity ID %s, got %s", tt.activityID, r.URL.Path)
				}

				w.WriteHeader(tt.responseStatus)
				w.Write([]byte(tt.responseBody))
			}))
			defer server.Close()

			// Override the ActivityUrl constant for testing
			originalActivityUrl := ActivityUrl
			defer func() { ActivityUrl = originalActivityUrl }()
			ActivityUrl = server.URL + "/activities/%s"

			client := NewStravaClient("test-token")
			activity, err := client.GetActivity(tt.activityID)

			if tt.expectError && err == nil {
				t.Error("expected error but got none")
			}
			if !tt.expectError && err != nil {
				t.Errorf("unexpected error: %v", err)
			}

			if !tt.expectError && activity.Name != tt.expectedName {
				t.Errorf("expected activity name %q, got %q", tt.expectedName, activity.Name)
			}
		})
	}
}

func TestBuildGpx_TimestampsAreRelativeToStartTime(t *testing.T) {
	startTime := time.Date(2024, 1, 15, 9, 0, 0, 0, time.UTC)

	streamPoints := []StravaStreamPoint{
		{Latitude: 37.83, Longitude: -122.26, Altitude: 10.5, Time: 0},
		{Latitude: 37.84, Longitude: -122.27, Altitude: 11.0, Time: 60},
		{Latitude: 37.85, Longitude: -122.28, Altitude: 11.5, Time: 120},
	}

	metadata := GpxMetadata{Name: "Test Run", Type: "Run", Time: startTime}

	gpxDoc, err := buildGpx(streamPoints, metadata)
	if err != nil {
		t.Fatalf("buildGpx failed: %v", err)
	}

	if len(gpxDoc.Tracks) != 1 || len(gpxDoc.Tracks[0].Segments) != 1 {
		t.Fatal("expected 1 track with 1 segment")
	}

	points := gpxDoc.Tracks[0].Segments[0].Points
	if len(points) != 3 {
		t.Fatalf("expected 3 points, got %d", len(points))
	}

	cases := []struct {
		idx      int
		expected time.Time
	}{
		{0, startTime},
		{1, startTime.Add(60 * time.Second)},
		{2, startTime.Add(120 * time.Second)},
	}

	for _, tc := range cases {
		got := points[tc.idx].Timestamp
		if !got.Equal(tc.expected) {
			t.Errorf("point[%d]: expected timestamp %v, got %v", tc.idx, tc.expected, got)
		}
	}
}

func TestBuildGpx_CoordinatesAndElevation(t *testing.T) {
	startTime := time.Date(2024, 1, 15, 9, 0, 0, 0, time.UTC)

	streamPoints := []StravaStreamPoint{
		{Latitude: 51.5074, Longitude: -0.1278, Altitude: 25.3, Time: 0},
		{Latitude: 51.5080, Longitude: -0.1285, Altitude: 28.1, Time: 30},
	}

	metadata := GpxMetadata{Name: "London Walk", Type: "Walk", Time: startTime}

	gpxDoc, err := buildGpx(streamPoints, metadata)
	if err != nil {
		t.Fatalf("buildGpx failed: %v", err)
	}

	points := gpxDoc.Tracks[0].Segments[0].Points

	if points[0].Latitude != 51.5074 {
		t.Errorf("expected latitude 51.5074, got %v", points[0].Latitude)
	}
	if points[0].Longitude != -0.1278 {
		t.Errorf("expected longitude -0.1278, got %v", points[0].Longitude)
	}
	if points[0].Elevation.Value() != 25.3 {
		t.Errorf("expected elevation 25.3, got %v", points[0].Elevation.Value())
	}

	if gpxDoc.Tracks[0].Name != "London Walk" {
		t.Errorf("expected track name %q, got %q", "London Walk", gpxDoc.Tracks[0].Name)
	}
}

const activityJSON = `{
	"id": 12345,
	"name": "Morning Run",
	"type": "Run",
	"start_date": "2024-01-15T09:00:00Z",
	"athlete": {"id": 67890}
}`

const streamsJSON = `[
	{"type": "latlng",    "data": [[37.83, -122.26], [37.84, -122.27]], "original_size": 2},
	{"type": "altitude",  "data": [10.5, 11.0],                         "original_size": 2},
	{"type": "time",      "data": [0, 60],                              "original_size": 2}
]`

func newMockStravaServer(t *testing.T, activityStatus int, activityBody string, streamsStatus int, streamsBody string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "streams") {
			w.WriteHeader(streamsStatus)
			w.Write([]byte(streamsBody))
		} else {
			w.WriteHeader(activityStatus)
			w.Write([]byte(activityBody))
		}
	}))
}

func overrideStravaURLs(t *testing.T, baseURL string) {
	t.Helper()
	origActivity := ActivityUrl
	origStreams := StreamsUrl
	ActivityUrl = baseURL + "/activities/%s"
	StreamsUrl = baseURL + "/activities/%s/streams"
	t.Cleanup(func() {
		ActivityUrl = origActivity
		StreamsUrl = origStreams
	})
}

func TestStravaClient_ExportActivityGPX_Success(t *testing.T) {
	server := newMockStravaServer(t, http.StatusOK, activityJSON, http.StatusOK, streamsJSON)
	defer server.Close()
	overrideStravaURLs(t, server.URL)

	client := NewStravaClient("test-token")
	gpxBytes, err := client.ExportActivityGPX("12345")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(gpxBytes) == 0 {
		t.Fatal("expected non-empty GPX output")
	}

	gpxStr := string(gpxBytes)
	if !strings.Contains(gpxStr, "<?xml") {
		t.Error("expected XML declaration in GPX output")
	}
	if !strings.Contains(gpxStr, "Morning Run") {
		t.Error("expected activity name in GPX output")
	}
	if !strings.Contains(gpxStr, "37.83") {
		t.Error("expected latitude in GPX output")
	}
	if !strings.Contains(gpxStr, "-122.26") {
		t.Error("expected longitude in GPX output")
	}
}

func TestStravaClient_ExportActivityGPX_TimestampCorrectness(t *testing.T) {
	server := newMockStravaServer(t, http.StatusOK, activityJSON, http.StatusOK, streamsJSON)
	defer server.Close()
	overrideStravaURLs(t, server.URL)

	client := NewStravaClient("test-token")
	gpxBytes, err := client.ExportActivityGPX("12345")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// startDate from activityJSON is "2024-01-15T09:00:00Z"
	// time stream is [0, 60], so timestamps should be 09:00:00 and 09:01:00
	gpxStr := string(gpxBytes)
	if !strings.Contains(gpxStr, "2024-01-15T09:00:00Z") {
		t.Error("expected first trackpoint at 2024-01-15T09:00:00Z (start_date + 0s)")
	}
	if !strings.Contains(gpxStr, "2024-01-15T09:01:00Z") {
		t.Error("expected second trackpoint at 2024-01-15T09:01:00Z (start_date + 60s)")
	}
}

func TestStravaClient_ExportActivityGPX_ActivityFetchError(t *testing.T) {
	server := newMockStravaServer(t, http.StatusNotFound, `{"error":"not found"}`, http.StatusOK, streamsJSON)
	defer server.Close()
	overrideStravaURLs(t, server.URL)

	client := NewStravaClient("test-token")
	_, err := client.ExportActivityGPX("99999")
	if err == nil {
		t.Error("expected error when activity fetch fails")
	}
}

func TestStravaClient_ExportActivityGPX_StreamFetchError(t *testing.T) {
	server := newMockStravaServer(t, http.StatusOK, activityJSON, http.StatusInternalServerError, `{"error":"server error"}`)
	defer server.Close()
	overrideStravaURLs(t, server.URL)

	client := NewStravaClient("test-token")
	_, err := client.ExportActivityGPX("12345")
	if err == nil {
		t.Error("expected error when stream fetch fails")
	}
}

func TestStravaClient_ExportActivityGPX_InvalidStartDate(t *testing.T) {
	badActivity := `{"id": 12345, "name": "Run", "type": "Run", "start_date": "not-a-date"}`
	server := newMockStravaServer(t, http.StatusOK, badActivity, http.StatusOK, streamsJSON)
	defer server.Close()
	overrideStravaURLs(t, server.URL)

	client := NewStravaClient("test-token")
	_, err := client.ExportActivityGPX("12345")
	if err == nil {
		t.Error("expected error for unparseable start_date")
	}
	if !strings.Contains(err.Error(), "parsing activity start date") {
		t.Errorf("expected parse error message, got: %v", err)
	}
}

func TestNewStravaClient(t *testing.T) {
	token := "test-token-123"
	client := NewStravaClient(token)

	if client.Token != token {
		t.Errorf("expected token %q, got %q", token, client.Token)
	}

	// Verify the client has an http.Client
	if client.client.Timeout != 0 {
		// Just checking that the client field exists and is initialized
	}
}
