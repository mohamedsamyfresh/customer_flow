import asyncio
import logging

import grpc

from app.analytics.notifier import (
    broadcast_dashboard_update,
    notify_db_dashboard_update,
)
from app.core.db import AsyncSessionLocal
from app.grpc import detection_pb2, detection_pb2_grpc
from app.services.detection_service import DetectionService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grpc_server")


class DetectionStreamServicer(
    detection_pb2_grpc.DetectionStreamServicer
):

    async def StreamDetections(
        self,
        request_iterator,
        context,
    ):
        logger.info(
            "ML gRPC stream connected: %s",
            context.peer(),
        )

        try:
            async for detection in request_iterator:
                try:
                    logger.info(
                        "Detection received from ML"
                    )

                    branch_id = None
                    async with AsyncSessionLocal() as db:
                        try:
                            service = DetectionService(db)
                            record = await service.process(
                                detection
                            )
                            branch_id = getattr(record, "branch_id", None)
                            await db.commit()
                            logger.info(
                                "Detection stored successfully: %s",
                                type(record).__name__,
                            )
                        except Exception:
                            await db.rollback()
                            raise

                        # Post-commit: trigger PostgreSQL notification for cross-container broadcast
                        # and broadcast in-process if manager has active local clients
                        try:
                            await notify_db_dashboard_update(db, branch_id=branch_id)
                            await broadcast_dashboard_update(db, branch_id=branch_id)
                        except Exception as ws_err:
                            logger.warning(
                                "WebSocket / Analytics notification failed (non-critical): %s",
                                ws_err,
                            )

                    yield detection_pb2.DetectionAck(
                        ok=True,
                        message="stored",
                    )

                except Exception as e:
                    logger.exception(
                        "Failed to process detection: %s",
                        e,
                    )

                    yield detection_pb2.DetectionAck(
                        ok=False,
                        message=str(e) or "failed to store detection",
                    )

        except asyncio.CancelledError:
            logger.info(
                "ML gRPC stream cancelled: %s",
                context.peer(),
            )
            raise

        except Exception:
            logger.exception(
                "Unexpected error in ML gRPC stream"
            )
            raise

        finally:
            logger.info(
                "ML gRPC stream closed: %s",
                context.peer(),
            )


async def serve(
    port: int = 50051,
):
    server = grpc.aio.server(
        options=[
            (
                "grpc.max_send_message_length",
                10 * 1024 * 1024,
            ),
            (
                "grpc.max_receive_message_length",
                10 * 1024 * 1024,
            ),
        ]
    )

    detection_pb2_grpc.add_DetectionStreamServicer_to_server(
        DetectionStreamServicer(),
        server,
    )

    server.add_insecure_port(
        f"0.0.0.0:{port}"
    )

    await server.start()

    logger.info(
        "gRPC server listening on 0.0.0.0:%s",
        port,
    )

    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())