package app

import (
	"context"
	"log/slog"
	"net/http"
	"os"

	"github.com/cderwin/skintrackr/app/clients"
	"github.com/cderwin/skintrackr/app/stores"
	"github.com/labstack/echo/v4"
	"github.com/labstack/echo/v4/middleware"
)

var (
	authUrl  = "https://www.strava.com/oauth/authorize"
	tokenUrl = "https://www.strava.com/oauth/token"
)

type ServerState struct {
	config       Config
	tokenStore   *stores.TokenStore
	stravaClient StravaClient
	s3Client     *clients.S3Client
}

func NewServer() ServerState {
	config := LoadConfig()

	redisClient, err := clients.NewRedisClient(config.UpstashRedisUrl)
	if err != nil {
		slog.Error("Cannot parse redis url", "upstash_redis_url", config.UpstashRedisUrl, "err", err)
		panic(err)
	}

	blobCfg := config.BlobStorageConfig
	s3Client, err := clients.NewS3Client(blobCfg.S3ApiEndpoint, "auto", blobCfg.AccessKeyId, blobCfg.SecretAccessKey, blobCfg.BucketName)
	if err != nil {
		slog.Error("Cannot create s3 client", "err", err)
		panic(err)
	}

	// Create a StravaClient without a token for OAuth and API requests
	stravaClient := NewStravaClient("")
	refresher := NewStravaTokenRefresher(config.StravaClientId, config.StravaClientSecret)
	tokenStore := stores.NewTokenStore(redisClient, config.Secret, refresher)

	return ServerState{
		config:       config,
		tokenStore:   tokenStore,
		stravaClient: stravaClient,
		s3Client:     s3Client,
	}
}

func (s *ServerState) RunForever() {
	e := echo.New()

	// setup logging middleware
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	e.Use(middleware.RequestLoggerWithConfig(middleware.RequestLoggerConfig{
		LogStatus:   true,
		LogURI:      true,
		LogError:    true,
		HandleError: true, // forwards error to the global error handler, so it can decide appropriate status code
		LogValuesFunc: func(c echo.Context, v middleware.RequestLoggerValues) error {
			if v.Error == nil {
				logger.LogAttrs(context.Background(), slog.LevelInfo, "REQUEST",
					slog.String("uri", v.URI),
					slog.Int("status", v.Status),
				)
			} else {
				logger.LogAttrs(context.Background(), slog.LevelError, "REQUEST_ERROR",
					slog.String("uri", v.URI),
					slog.Int("status", v.Status),
					slog.String("err", v.Error.Error()),
				)
			}
			return nil
		},
	}))

	// middleware
	e.Use(middleware.Recover())

	// static files
	e.Static("/static", "/usr/src/static")
	e.File("/", "/usr/src/static/index.html")

	// dynamic routes
	e.GET("/healthcheck", handleHealthcheck)
	e.GET("/oauth2/connect", s.handleConnect)
	e.GET("/oauth2/callback", s.handleCallback)
	e.GET("/subscriptions/callback", s.handleSubscriptionCallback)
	e.POST("/subscriptions/callback", s.handlePushEvent)

	// token generation API
	e.GET("/token/new", s.handleTokenStart)
	e.GET("/token/callback", s.handleTokenCallback)
	e.POST("/token/verify", s.handleTokenVerify)
	e.POST("/token/revoke", s.handleTokenRevoke)
	e.GET("/api/strava-token", s.handleStravaToken)
	e.GET("/api/activity/:activityId/export", s.handleExportTrack)
	e.POST("/api/activity/:activityId/persist", s.handlePersistActivity)

	slog.Info("Establishing subscriptions in background")
	go EstablishSubscriptions(&s.config, &s.stravaClient)

	slog.Info("starting server", "port", 8080)
	e.Logger.Fatal(e.Start(":8080"))
}

func handleHealthcheck(c echo.Context) error {
	response := struct {
		Ok bool `json:"ok"`
	}{Ok: true}
	c.JSON(http.StatusOK, response)
	return nil
}
