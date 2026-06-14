# API Endpoints

This doc tracks the overall api architecture of the skintrackr backend.

GET /healthcheck
GET /oauth2/connect
GET /oauth2/callback
GET /subscriptions/callback
POST /subscriptions/callback
GET /tokens/new
GET /tokens/callback
GET /tokens/poll
POST /tokens/verify
POST /tokens/revoke
GET /api/strava-token

GET /tiles/{z}/{x}/{y} - tileserver
POST /tiles/refresh - regenerate MVT files
GET /tracks/{activity_id}
GET /tracks/{activity_id}/metadata
GET /tracks/list?filters
DELETE /tracks/{activity_id}
POST /tracks/sync?activityId={activityId}

