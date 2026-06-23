// Package clients isolates all use of the external redis and AWS/S3 client
// libraries. Nothing outside this package should import those libraries.
package clients

import (
	"context"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

// ErrNotFound is returned when a requested key does not exist. It replaces the
// redis-specific redis.Nil sentinel so callers need not import the redis library.
var ErrNotFound = errors.New("key not found")

// RedisClient wraps the redis client library, exposing only the primitive
// operations the stores require.
type RedisClient struct {
	client *redis.Client
	ctx    context.Context
}

// NewRedisClient parses a redis connection URL and returns a connected client.
func NewRedisClient(url string) (*RedisClient, error) {
	options, err := redis.ParseURL(url)
	if err != nil {
		return nil, err
	}
	return NewRedisClientFromClient(redis.NewClient(options)), nil
}

// NewRedisClientFromClient wraps an existing redis client. Useful for tests
// that inject a fake or miniredis-backed client.
func NewRedisClientFromClient(client *redis.Client) *RedisClient {
	return &RedisClient{client: client, ctx: context.Background()}
}

// HSet sets the given fields on the hash at key.
func (r *RedisClient) HSet(key string, fields map[string]string) error {
	values := make([]any, 0, len(fields)*2)
	for k, v := range fields {
		values = append(values, k, v)
	}
	return r.client.HSet(r.ctx, key, values...).Err()
}

// HGetAll returns all fields of the hash at key, or ErrNotFound if it is empty.
func (r *RedisClient) HGetAll(key string) (map[string]string, error) {
	result, err := r.client.HGetAll(r.ctx, key).Result()
	if err != nil {
		return nil, err
	}
	if len(result) == 0 {
		return nil, ErrNotFound
	}
	return result, nil
}

// Set stores value at key with the given expiration (0 means no expiration).
func (r *RedisClient) Set(key, value string, ttl time.Duration) error {
	return r.client.Set(r.ctx, key, value, ttl).Err()
}

// GetDel atomically fetches and deletes key, returning ErrNotFound if missing.
func (r *RedisClient) GetDel(key string) (string, error) {
	value, err := r.client.GetDel(r.ctx, key).Result()
	if err == redis.Nil {
		return "", ErrNotFound
	}
	return value, err
}

// Exists reports whether key exists.
func (r *RedisClient) Exists(key string) (bool, error) {
	count, err := r.client.Exists(r.ctx, key).Result()
	if err != nil {
		return false, err
	}
	return count > 0, nil
}

// TTL returns the remaining time to live of key.
func (r *RedisClient) TTL(key string) (time.Duration, error) {
	return r.client.TTL(r.ctx, key).Result()
}

// Expire sets a timeout on key.
func (r *RedisClient) Expire(key string, ttl time.Duration) error {
	return r.client.Expire(r.ctx, key, ttl).Err()
}
