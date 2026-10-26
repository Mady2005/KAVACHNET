"""Small explicit policy for simulated delivery decisions; not a learned model."""
from app.schemas.network import CommunicationDecision, DeliveryHandling, NetworkState

def decide_delivery(network_state:NetworkState,priority:str,compression_available:bool,queue_depth:int)->CommunicationDecision:
    compressed=compression_available
    if network_state==NetworkState.DISCONNECTED:
        handling=DeliveryHandling.DEFER
        reason="Simulated channel is disconnected; preserve the message in the durable queue."
    elif network_state==NetworkState.DEGRADED and priority in {"P0","P1"}:
        handling=DeliveryHandling.SEND_PRIORITY
        reason="Degraded channel; strict priority ordering gives this message earlier eligibility."
    elif network_state==NetworkState.DEGRADED and compression_available:
        handling=DeliveryHandling.SEND_COMPRESSED
        reason="Degraded channel; send the eligible message using its stored representation."
    else:
        handling=DeliveryHandling.SEND
        reason="Simulated channel is available; send using the locally stored representation."
    return CommunicationDecision(network_state=network_state,message_priority=priority,recommended_handling=handling,reason=reason,compression_selected=compressed,delivery_deferred=handling==DeliveryHandling.DEFER,queue_depth=queue_depth)
