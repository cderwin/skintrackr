"""Strava webhooks: the subscription, its verification callback, and inbound push events.

Activity `create`/`update` events archive the activity (see `BlobStore.persist_activity`) in the background,
since Strava expects a prompt acknowledgement and retries the webhook on timeout.
"""

from typing import Annotated, Any

from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import QueryParameter

from skintrackr.logging import get_logger
from skintrackr.models import PushEvent, loads
from skintrackr.server.services import Services, get_services

logger = get_logger(__name__)

CALLBACK_PATH = "/subscriptions/callback"


@get(CALLBACK_PATH)
async def subscription_callback(
    state: State,
    verify_token: Annotated[str | None, QueryParameter(name="hub.verify_token")] = None,
    challenge: Annotated[str, QueryParameter(name="hub.challenge")] = "",
) -> dict[str, str]:
    """Answer Strava's check of a new subscription by echoing its challenge."""
    if verify_token != get_services(state).config.verify_token:
        logger.warning("received subscription callback with incorrect verify_token")
        raise ClientException("hub.verify_token is incorrect")
    return {"hub.challenge": challenge}


@post(CALLBACK_PATH, status_code=200)
async def push_event(request: Request, state: State) -> None:
    """Receive a webhook event. Always answers 200 so Strava doesn't retry."""
    try:
        event = loads(await request.body(), PushEvent)
    except Exception as e:
        logger.warning("webhook received: undecodable event", extra={"err": str(e)})
        return

    match event.object_type:
        case "activity":
            logger.info(
                "webhook received: activity update",
                extra={"athlete_id": event.owner_id, "activity_id": event.object_id, "aspect_type": event.aspect_type},
            )
            if event.aspect_type in ("create", "update"):
                services = get_services(state)
                services.spawn(persist_activity_event(services, event), name=f"persist-activity-{event.object_id}")
        case "athlete":
            logger.info("webhook received: athlete revoked access", extra={"athlete_id": event.owner_id})
        case _:
            logger.warning("webhook received: unrecognized object type", extra={"object_type": event.object_type})


async def persist_activity_event(services: Services, event: PushEvent) -> None:
    """Archive the activity an event is about. Errors are logged: there's no caller to report them to."""
    context: dict[str, Any] = {"athlete_id": event.owner_id, "activity_id": event.object_id}
    try:
        strava = await services.strava_for(event.owner_id)
    except Exception as e:
        logger.error("error fetching strava token for activity persist", exc_info=e, extra=context)
        return
    try:
        await services.blob_store.persist_activity(strava, str(event.object_id))
    except Exception as e:
        logger.error("error persisting activity from webhook", exc_info=e, extra=context)
        return
    logger.info("persisted activity from webhook", extra=context)


async def establish_subscriptions(services: Services) -> None:
    """Make sure the app has a webhook subscription, creating one if not. Errors are logged.

    Run in the background at startup: creating a subscription makes Strava call back into this server.
    """
    logger.info("fetching current subscription info")
    try:
        subscriptions = await services.strava.list_subscriptions()
    except Exception as e:
        logger.error("error fetching subscription", exc_info=e)
        return
    if subscriptions:
        logger.info("fetched current subscription", extra={"subscription_id": subscriptions[0].id})
        return

    logger.info("no existing subscription found, will attempt to create one")
    try:
        subscription = await services.strava.create_subscription(
            services.config.url(CALLBACK_PATH), services.config.verify_token
        )
    except Exception as e:
        logger.error("error creating subscription", exc_info=e)
        return
    logger.info("created new subscription", extra={"subscription_id": subscription.id})


webhook_handlers = [subscription_callback, push_event]
