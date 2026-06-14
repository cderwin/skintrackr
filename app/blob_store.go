package app

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/tkrajina/gpxgo/gpx"
)

type BlobStore struct {
	config *Config
	strava *StravaClient
}

func (bs *BlobStore) PersistActivity(activityId string) error {
	// create temporary directory for activity files
	tempDir, err := os.MkdirTemp("", "skintrackr-persist-activity-*")
	if err != nil {
		return fmt.Errorf("failed to create temp dir: %w", err)
	}
	defer os.RemoveAll(tempDir) // cleans up tmpdir and activity files

	// download activity metadata as json file
	activityData, err := bs.strava.GetActivity(activityId)
	if err != nil {
		return fmt.Errorf("failed to retrieve activity: %w", err)
	}

	data, err := json.Marshal(activityData)
	if err != nil {
		return fmt.Errorf("error marshaling activity: %w", err)
	}

	activityDataPath := path.Join(tempDir, "activity_metadata.json")
	if err := os.WriteFile(activityDataPath, data, 0644); err != nil {
		return fmt.Errorf("error writing activity to %s: %w", activityDataPath, err)
	}

	// download gpx track for activity
	activityTrackPath := path.Join(tempDir, "activity_track.gpx")
	gpxDoc, err := bs.strava.ExportActivityGPX(&activityData)
	if err != nil {
		return fmt.Errorf("failed to download activity track: %w", err)
	}

	bytes, err := gpxDoc.ToXml(gpx.ToXmlParams{})
	if err != nil {
		return err
	}

	err = os.WriteFile(activityTrackPath, bytes, 0644)
	if err != nil {
		return err
	}

	// Upload files to s3
	activityMetadataBlobKey := fmt.Sprintf("activities/%d/metadata/%d.json", activityData.Athlete.Id, activityData.Id)
	activityTrackBlobKey := fmt.Sprintf("activities/%d/tracks/%d.gpx", activityData.Athlete.Id, activityData.Id)
	if err := bs.writeFileToBlob(activityMetadataBlobKey, activityDataPath); err != nil {
		return fmt.Errorf("failed to upload activity metadata: %w", err)
	}
	if err := bs.writeFileToBlob(activityTrackBlobKey, activityTrackPath); err != nil {
		return fmt.Errorf("failed to upload activity track: %w", err)
	}

	return nil
}

// writes the file at `filePath` to the blob storage bucket at location `key`
func (bs *BlobStore) writeFileToBlob(key string, filePath string) error {
	blobCfg := bs.config.BlobStorageConfig

	cfg, err := awsconfig.LoadDefaultConfig(
		context.Background(),
		awsconfig.WithRegion("auto"),
		awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(blobCfg.AccessKeyId, blobCfg.SecretAccessKey, ""),
		),
	)
	if err != nil {
		return fmt.Errorf("failed to load aws config: %w", err)
	}

	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		o.BaseEndpoint = aws.String(blobCfg.S3ApiEndpoint)
	})

	file, err := os.Open(filePath)
	if err != nil {
		return fmt.Errorf("failed to open %s: %w", filePath, err)
	}
	defer file.Close()

	_, err = client.PutObject(context.Background(), &s3.PutObjectInput{
		Bucket: aws.String(blobCfg.BucketName),
		Key:    aws.String(key),
		Body:   file,
	})
	if err != nil {
		return fmt.Errorf("failed to upload %s to s3://%s/%s: %w", filePath, blobCfg.BucketName, key, err)
	}

	return nil
}
