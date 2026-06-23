package clients

import (
	"context"
	"fmt"
	"io"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

// S3Client wraps the AWS SDK v2 s3 client against an S3-compatible endpoint,
// exposing only the operations the stores require.
type S3Client struct {
	client *s3.Client
	bucket string
}

// NewS3Client builds an s3 client for the given endpoint, credentials and bucket.
func NewS3Client(endpoint, region, accessKeyId, secretAccessKey, bucket string) (*S3Client, error) {
	cfg, err := awsconfig.LoadDefaultConfig(
		context.Background(),
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(accessKeyId, secretAccessKey, ""),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to load aws config: %w", err)
	}

	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		o.BaseEndpoint = aws.String(endpoint)
	})

	return &S3Client{client: client, bucket: bucket}, nil
}

// PutObject uploads body to the configured bucket at key.
func (c *S3Client) PutObject(ctx context.Context, key string, body io.Reader) error {
	_, err := c.client.PutObject(ctx, &s3.PutObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
		Body:   body,
	})
	if err != nil {
		return fmt.Errorf("failed to upload to s3://%s/%s: %w", c.bucket, key, err)
	}
	return nil
}
