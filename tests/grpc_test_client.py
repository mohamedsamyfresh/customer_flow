"""
Standalone gRPC test client for DetectionStream service.

Sends realistic sample DetectionMessage payloads over a persistent
bidirectional gRPC stream to localhost:50051 and prints received DetectionAck responses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so app modules can be imported
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import grpc  # noqa: E402

from app.grpc import detection_pb2, detection_pb2_grpc  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("grpc_test_client")


def build_sample_messages() -> list[detection_pb2.DetectionMessage]:
    """
    Construct realistic sample detection messages conforming to detection.proto.
    Covers:
      1. CustomerFlow - Entry only
      2. CustomerFlow - Exit update for the same entry (correlated by exit_count)
      3. CustomerFlow - Complete entry and exit together
      4. WaitingSession - Queue waiting time record
    """
    # 1. CustomerFlow: Entry only
    entry_msg = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            entry_time="2026-08-26T11:00:00Z",
            entry_count=101,
            age_class="25-35",
            gender="female",
            gender_conf=0.96,
            enter_emotion="happy",
            enter_emotion_conf=0.88,
            entry_face_box=[100.0, 120.0, 200.0, 220.0],
            entry_face_vector=[0.05, 0.12, -0.23, 0.88],
        )
    )

    # 2. CustomerFlow: Exit correlation for entry_count=101
    exit_msg = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            exit_time="2026-08-26T11:15:00Z",
            exit_count=101,
            exit_emotion="neutral",
            exit_emotion_conf=0.91,
            exit_face_box=[102.0, 125.0, 205.0, 222.0],
            exit_face_vector=[0.06, 0.11, -0.21, 0.85],
            face_match_score=0.94,
        )
    )

    # 3. CustomerFlow: Complete message (both entry and exit together)
    complete_msg = detection_pb2.DetectionMessage(
        customer_flow=detection_pb2.CustomerFlow(
            entry_time="2026-08-26T11:05:00Z",
            entry_count=102,
            age_class="35-45",
            gender="male",
            gender_conf=0.92,
            enter_emotion="neutral",
            enter_emotion_conf=0.85,
            entry_face_box=[150.0, 160.0, 250.0, 260.0],
            entry_face_vector=[-0.10, 0.45, 0.33, -0.02],
            exit_time="2026-08-26T11:20:00Z",
            exit_count=102,
            exit_emotion="happy",
            exit_emotion_conf=0.89,
            exit_face_box=[152.0, 162.0, 252.0, 262.0],
            exit_face_vector=[-0.09, 0.44, 0.34, -0.01],
            face_match_score=0.96,
        )
    )

    # 4. WaitingSession: Waiting queue session
    waiting_msg = detection_pb2.DetectionMessage(
        waiting_session=detection_pb2.WaitingSession(
            id=1,
            entry_frame=150,
            exit_frame=450,
            entry_time="2026-08-26T11:10:00",
            exit_time="2026-08-26T11:12:30",
            duration="00:02:30",
            duration_s=150.0,
        )
    )

    return [entry_msg, exit_msg, complete_msg, waiting_msg]


async def run_client(target: str = "localhost:50051") -> None:
    """
    Connects to the gRPC DetectionStream server, streams DetectionMessage
    payloads over a single persistent bidirectional stream, and prints ACKs.
    """
    logger.info("Connecting to DetectionStream server at %s...", target)
    sample_messages = build_sample_messages()

    async def request_generator():
        for i, msg in enumerate(sample_messages, start=1):
            payload_field = msg.WhichOneof("payload")
            logger.info("Sending Detection #%d [payload: %s]", i, payload_field)
            yield msg
            # Small delay between messages to simulate real-time ML detection stream
            await asyncio.sleep(0.3)

    try:
        async with grpc.aio.insecure_channel(target) as channel:
            stub = detection_pb2_grpc.DetectionStreamStub(channel)
            call = stub.StreamDetections(request_generator())

            ack_index = 0
            async for ack in call:
                ack_index += 1
                print(f'ACK #{ack_index}: ok={ack.ok}, message="{ack.message}"')

            logger.info(
                "Completed streaming: sent %d messages, received %d ACKs.",
                len(sample_messages),
                ack_index,
            )

    except grpc.aio.AioRpcError as rpc_error:
        logger.error(
            "gRPC RPC error encountered: code=%s, details=%s",
            rpc_error.code(),
            rpc_error.details(),
        )
    except Exception as e:
        logger.error("Unexpected client error: %s", e)


def main():
    target = os.getenv("GRPC_TARGET", "localhost:50051")
    asyncio.run(run_client(target))


if __name__ == "__main__":
    main()
