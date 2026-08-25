"""Channels of Nodya: per-chat receiving and delivery.

One subpackage per channel (telegram/, later browser/, discord/)
keeps the webhook (HTTP intake in the Gateway) and the sender
(RabbitMQ consumer for delivery) together. Only the queue connects
the two sides.
"""
