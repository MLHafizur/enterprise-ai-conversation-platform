"""Runnable leased worker for durable repository security scans."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta
from typing import cast

import httpx

from enterprise_chat_platform.application.code_security import TenantCodeSecurityPolicy
from enterprise_chat_platform.application.code_security_ai import (
    DisabledSecurityExplanationProvider,
    LocalSecurityExplanationProvider,
    SecurityExplanationProvider,
)
from enterprise_chat_platform.application.process_code_scan import (
    CodeScanWorkerConfig,
    CodeScanWorkResult,
    ProcessCodeScanWorker,
    RepositoryScanner,
    RepositoryScannerFactory,
)
from enterprise_chat_platform.domain.code_security import SecurityAnalysisSettings
from enterprise_chat_platform.infrastructure.code_analysis.gemini_explanations import (
    GeminiSecurityExplanationProvider,
)
from enterprise_chat_platform.infrastructure.code_analysis.repository_scanner import (
    BoundedGitRepositoryScanner,
    RepositoryScanLimits,
)
from enterprise_chat_platform.infrastructure.code_analysis.result_notifications import (
    AsyncNotificationClient,
    ExternalSecurityResultNotifier,
)
from enterprise_chat_platform.infrastructure.code_analysis.security_engine import SecurityEngine
from enterprise_chat_platform.infrastructure.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.code_security import (
    SqlAlchemyCodeSecurityStore,
)
from enterprise_chat_platform.infrastructure.realtime.code_scan_publisher import (
    RedisCodeScanEventPublisher,
    RedisPublisherClient,
)
from enterprise_chat_platform.infrastructure.realtime.runtime import create_redis_client
from enterprise_chat_platform.infrastructure.runtime_adapters import UtcSystemClock
from enterprise_chat_platform.observability import configure_logging
from enterprise_chat_platform.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def _worker_config(settings: Settings) -> CodeScanWorkerConfig:
    return CodeScanWorkerConfig(
        lease_duration=timedelta(seconds=settings.code_security_worker_lease_seconds),
        max_attempts=settings.code_security_worker_max_attempts,
        initial_backoff=timedelta(seconds=settings.code_security_worker_initial_backoff_seconds),
        max_backoff=timedelta(seconds=settings.code_security_worker_max_backoff_seconds),
        allowed_git_hosts=settings.allowed_code_repository_hosts,
    )


def _scanner_factory(settings: Settings) -> RepositoryScannerFactory:
    def build(policy: TenantCodeSecurityPolicy) -> RepositoryScanner:
        max_file_bytes = min(
            policy.max_file_bytes,
            settings.code_security_max_file_size_bytes,
        )
        max_files = min(
            policy.max_repository_files,
            settings.code_security_max_repository_files,
        )
        max_repository_bytes = min(
            policy.max_repository_bytes,
            settings.code_security_max_repository_bytes,
        )
        return BoundedGitRepositoryScanner(
            engine=SecurityEngine(
                settings=SecurityAnalysisSettings(
                    max_code_bytes=max_file_bytes,
                    max_findings=min(settings.code_security_max_findings_per_scan, 10_000),
                )
            ),
            limits=RepositoryScanLimits(
                clone_timeout_seconds=settings.code_security_clone_timeout_seconds,
                scan_timeout_seconds=settings.code_security_scan_timeout_seconds,
                max_file_size_bytes=max_file_bytes,
                max_files=max_files,
                max_repository_bytes=max_repository_bytes,
                max_findings=settings.code_security_max_findings_per_scan,
            ),
        )

    return build


def _explanation_provider(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None,
) -> SecurityExplanationProvider:
    if settings.code_security_ai_mode == "disabled":
        return DisabledSecurityExplanationProvider()
    if settings.code_security_ai_mode == "echo":
        return LocalSecurityExplanationProvider()
    if http_client is None or settings.gemini_api_key is None:
        raise RuntimeError("Gemini security explanations require an HTTP client and API key")
    return GeminiSecurityExplanationProvider(
        client=http_client,
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        timeout_seconds=settings.provider_timeout_seconds,
        api_base_url=settings.gemini_api_base_url,
    )


async def run_code_security_worker(
    *,
    settings: Settings | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)
    if not resolved.code_security_enabled:
        raise RuntimeError("Code security worker is disabled")

    shutdown = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, shutdown.set)
            except NotImplementedError:  # pragma: no cover - Windows fallback
                pass

    engine = create_database_engine(resolved)
    session_factory = create_session_factory(engine)
    needs_http_client = (
        resolved.code_security_ai_mode == "gemini"
        or resolved.code_security_github_token is not None
        or resolved.code_security_slack_webhook_url is not None
    )
    http_client = httpx.AsyncClient() if needs_http_client else None
    redis_client = create_redis_client(resolved) if resolved.realtime_enabled else None
    publisher = (
        RedisCodeScanEventPublisher(cast(RedisPublisherClient, redis_client))
        if redis_client is not None
        else None
    )
    result_notifier = (
        ExternalSecurityResultNotifier(
            client=cast(AsyncNotificationClient, http_client),
            github_token=(
                resolved.code_security_github_token.get_secret_value()
                if resolved.code_security_github_token is not None
                else None
            ),
            github_api_base_url=resolved.code_security_github_api_base_url,
            slack_webhook_url=(
                resolved.code_security_slack_webhook_url.get_secret_value()
                if resolved.code_security_slack_webhook_url is not None
                else None
            ),
            allowed_git_hosts=resolved.allowed_code_repository_hosts,
        )
        if http_client is not None
        and (
            resolved.code_security_github_token is not None
            or resolved.code_security_slack_webhook_url is not None
        )
        else None
    )
    worker = ProcessCodeScanWorker(
        store=SqlAlchemyCodeSecurityStore(session_factory),
        scanner_factory=_scanner_factory(resolved),
        explanation_provider=_explanation_provider(resolved, http_client=http_client),
        clock=UtcSystemClock(),
        config=_worker_config(resolved),
        publisher=publisher,
        result_notifier=result_notifier,
    )

    logger.info("code security worker started")
    try:
        while not shutdown.is_set():
            try:
                result = await worker.process_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("code security worker poll failed")
                result = CodeScanWorkResult.NO_WORK
            if result is CodeScanWorkResult.NO_WORK:
                try:
                    await asyncio.wait_for(
                        shutdown.wait(),
                        timeout=resolved.code_security_worker_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        if http_client is not None:
            await http_client.aclose()
        await engine.dispose()
        logger.info("code security worker stopped")


def main() -> None:
    asyncio.run(run_code_security_worker())


if __name__ == "__main__":  # pragma: no cover
    main()
