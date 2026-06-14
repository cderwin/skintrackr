export def main [] {}

export def "main login" [] {}

export def "main token" [] {
    http get --headers {Authorization: $"Bearer ($env.SKINTRACKR_TOKEN)"} "https://skintrackr.fly.dev/api/strava-token"
}

