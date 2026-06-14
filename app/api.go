package app

import (
	"fmt"
	"log/slog"
	"net/http"

	"github.com/labstack/echo/v4"
	"github.com/tkrajina/gpxgo/gpx"
)

func (s *ServerState) handleExportTrack(c echo.Context) error {
	tokenInfo, err := s.AuthenticateRequest(c.Request())
	if err != nil {
		return err
	}

	activityId := c.Param("activityId")
	if activityId == "" {
		return echo.NewHTTPError(http.StatusBadRequest, "activityId path parameter is required")
	}

	stravaToken, err := s.store.FetchToken(tokenInfo.athleteId)
	if err != nil {
		slog.Error("error fetching strava token for export", "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to fetch Strava token")
	}

	client := NewStravaClient(stravaToken)
	activity, err := client.GetActivity(activityId)
	if err != nil {
		slog.Error("error fetching activity for export", "activity_id", activityId, "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to fetch activity")
	}

	gpxDoc, err := client.ExportActivityGPX(&activity)
	if err != nil {
		slog.Error("error exporting activity as GPX", "activity_id", activityId, "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to export activity")
	}

	gpxBytes, err := gpxDoc.ToXml(gpx.ToXmlParams{})
	if err != nil {
		slog.Error("error serializing activity GPX", "activity_id", activityId, "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to export activity")
	}

	filename := fmt.Sprintf("activity-%s.gpx", activityId)
	c.Response().Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	return c.Blob(http.StatusOK, "application/gpx+xml", gpxBytes)
}

// handlePersistActivity downloads an activity's metadata and GPX track from
// Strava and uploads them to blob storage via BlobStore.PersistActivity.
//
// Route: POST /api/activity/:activityId/persist
func (s *ServerState) handlePersistActivity(c echo.Context) error {
	tokenInfo, err := s.AuthenticateRequest(c.Request())
	if err != nil {
		return err
	}

	activityId := c.Param("activityId")
	if activityId == "" {
		return echo.NewHTTPError(http.StatusBadRequest, "activityId path parameter is required")
	}

	stravaToken, err := s.store.FetchToken(tokenInfo.athleteId)
	if err != nil {
		slog.Error("error fetching strava token for persist", "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to fetch Strava token")
	}

	client := NewStravaClient(stravaToken)
	blobStore := BlobStore{config: &s.config, strava: &client}
	if err := blobStore.PersistActivity(activityId); err != nil {
		slog.Error("error persisting activity", "activity_id", activityId, "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to persist activity")
	}

	response := struct {
		Ok         bool   `json:"ok"`
		ActivityId string `json:"activityId"`
	}{Ok: true, ActivityId: activityId}
	return c.JSON(http.StatusOK, response)
}
