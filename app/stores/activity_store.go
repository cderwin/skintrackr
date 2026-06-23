package stores

import (
	"bytes"
	"context"
	"fmt"

	"github.com/cderwin/skintrackr/app/clients"
)

// ActivityArchive is the serialized form of a Strava activity, ready to be
// persisted to blob storage.
type ActivityArchive struct {
	AthleteId  int
	ActivityId int
	Metadata   []byte // activity metadata as JSON
	Track      []byte // activity GPX track as XML
}

// ActivitySource fetches an activity from Strava and serializes it for storage.
// It is satisfied by an adapter in the app package so the store need not know
// about Strava HTTP requests or GPX serialization.
type ActivitySource interface {
	FetchActivityArchive(activityId string) (ActivityArchive, error)
}

// ActivityStore archives Strava activities (metadata + GPX track) to blob storage.
type ActivityStore struct {
	blob   *clients.S3Client
	source ActivitySource
}

func NewActivityStore(blob *clients.S3Client, source ActivitySource) *ActivityStore {
	return &ActivityStore{blob: blob, source: source}
}

// PersistActivity downloads an activity's metadata and GPX track and uploads
// them to blob storage under activities/{athleteId}/...
func (s *ActivityStore) PersistActivity(activityId string) error {
	archive, err := s.source.FetchActivityArchive(activityId)
	if err != nil {
		return fmt.Errorf("failed to retrieve activity: %w", err)
	}

	metadataKey := fmt.Sprintf("activities/%d/metadata/%d.json", archive.AthleteId, archive.ActivityId)
	trackKey := fmt.Sprintf("activities/%d/tracks/%d.gpx", archive.AthleteId, archive.ActivityId)

	if err := s.blob.PutObject(context.Background(), metadataKey, bytes.NewReader(archive.Metadata)); err != nil {
		return fmt.Errorf("failed to upload activity metadata: %w", err)
	}
	if err := s.blob.PutObject(context.Background(), trackKey, bytes.NewReader(archive.Track)); err != nil {
		return fmt.Errorf("failed to upload activity track: %w", err)
	}

	return nil
}
