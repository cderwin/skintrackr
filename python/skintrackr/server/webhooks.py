"""Strava webhooks: the subscription, its verification callback, and inbound push events.

Strava has one subscription per app, which delivers events for every athlete who authorized it. Events are
handled in the background (see `ingest.py`), since Strava expects a prompt acknowledgement and retries on
timeout:

- activity `create`/`update`: archive the activity and update the athlete's index
- activity `delete`: remove it from both
- athlete deauthorization (`updates: {"authorized": "false"}`): delete everything stored for the athlete
"""

from collections.abc import Awaitable
from typing import Annotated

from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import ClientException
from litestar.params import QueryParameter

from skintrackr.logging import get_logger
from skintrackr.models import PushEvent, loads
from skintrackr.server.ingest import ingest_activity, remove_activity, remove_athlete
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

    services = get_services(state)
    context: dict[str, object] = {"athlete_id": event.owner_id, "object_type": event.object_type, "aspect_type": event.aspect_type}
    if event.object_type == "activity":
        context["activity_id"] = event.object_id
    logger.info("webhook received", extra=context)

    match (event.object_type, event.aspect_type):
        case ("activity", "create" | "update"):
            work = _ingest_event(services, event)
        case ("activity", "delete"):
            work = remove_activity(services, event.owner_id, event.object_id)
        case ("athlete", _) if str(event.updates.get("authorized", "")).lower() == "false":
            work = remove_athlete(services, event.owner_id)
        case _:
            return
    services.spawn(_logged(work, context), name=f"webhook-{event.object_type}-{event.object_id}")


async def _ingest_event(services: Services, event: PushEvent) -> None:
    strava = await services.strava_for(event.owner_id)
    await ingest_activity(services, strava, event.owner_id, event.object_id)


async def _logged(work: Awaitable[None], context: dict[str, object]) -> None:
    """Run an event's work, logging the outcome: there's no caller to report errors to."""
    try:
        await work
    except Exception as e:
        logger.error("error handling webhook event", exc_info=e, extra=context)
        return
    logger.info("handled webhook event", extra=context)


SUBSCRIBE_TASK = "establish-subscriptions"


def ensure_subscription(services: Services) -> None:
    """Make sure the app's webhook subscription exists, in the background (unless that's already underway)."""
    if not services.is_running(SUBSCRIBE_TASK):
        services.spawn(establish_subscriptions(services), name=SUBSCRIBE_TASK)


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
