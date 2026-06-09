package app

import (
	"fmt"
	"log/slog"
	"net/http"

	"github.com/labstack/echo/v4"
)

func (s *ServerState) handleExportTrack(c echo.Context) error {
	tokenInfo, err := s.AuthenticateRequest(c.Request())
	if err != nil {
		return err
	}

	activityId := c.QueryParam("activityId")
	if activityId == "" {
		return echo.NewHTTPError(http.StatusBadRequest, "activityId query parameter is required")
	}

	stravaToken, err := s.store.FetchToken(tokenInfo.athleteId)
	if err != nil {
		slog.Error("error fetching strava token for export", "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to fetch Strava token")
	}

	client := NewStravaClient(stravaToken)
	gpxBytes, err := client.ExportActivityGPX(activityId)
	if err != nil {
		slog.Error("error exporting activity as GPX", "activity_id", activityId, "athlete_id", tokenInfo.athleteId, "err", err)
		return echo.NewHTTPError(http.StatusInternalServerError, "failed to export activity")
	}

	filename := fmt.Sprintf("activity-%s.gpx", activityId)
	c.Response().Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	return c.Blob(http.StatusOK, "application/gpx+xml", gpxBytes)
}
