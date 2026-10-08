"""Native Bridge — internal staged transport contract for the AutoCAD .NET bridge.
Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:43
"""

from .client import BridgeClientProtocolError, BridgeRemoteError, NativeBridgeClient
from .protocol import (
    MAX_FRAME_BYTES,
    NATIVE_PROTOCOL_VERSION,
    BridgeProtocolError,
    BridgeRequest,
    BridgeResponse,
    DocumentIdentityParams,
    decode_frame,
    encode_frame,
)

__all__ = [
    "BridgeClientProtocolError",
    "BridgeRemoteError",
    "NativeBridgeClient",
    "MAX_FRAME_BYTES",
    "NATIVE_PROTOCOL_VERSION",
    "BridgeProtocolError",
    "BridgeRequest",
    "BridgeResponse",
    "DocumentIdentityParams",
    "decode_frame",
    "encode_frame",
    "NamedPipeTransport",
    "pipe_name_for_session",
]

from .transport_windows import NamedPipeTransport, pipe_name_for_session
