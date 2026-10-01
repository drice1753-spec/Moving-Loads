"""Autonomous email system for truckload product offerings.

Turns a product deal (flooring, tile, pavers, vanities, lumber ...) into a
customer-facing offering email, sends it to a buyer list, reads the replies,
answers delivered-price questions using the freight calculator in
``moving_loads.freight_calculator``, escalates firm offers to a human, and
follows up with buyers who have not answered.

Safety model (see docs/EMAIL_OFFERINGS_DESIGN.md):

* Dry-run by default. Nothing reaches Gmail unless ``Policy.live`` is true.
* Buyer-facing automatic replies are Gmail *drafts* unless
  ``Policy.auto_reply_mode == "send"``.
* Firm offers are never accepted automatically; they are escalated.
* Internal cost data is never rendered into a customer-facing email.
"""

from email_offerings.models import (
    ActionKind,
    ActionRecord,
    Buyer,
    BuyerStatus,
    Campaign,
    CampaignKind,
    CampaignStatus,
    Classification,
    Contact,
    ContactKind,
    DeliveredQuote,
    InboundMessage,
    Offering,
    OfferingStatus,
    OutboundMessage,
    ReplyIntent,
    RunReport,
    SendResult,
    Unit,
    make_offering_id,
    utcnow,
)
from email_offerings.config import Policy, Settings

__all__ = [
    "ActionKind",
    "ActionRecord",
    "Buyer",
    "BuyerStatus",
    "Campaign",
    "CampaignKind",
    "CampaignStatus",
    "Classification",
    "Contact",
    "ContactKind",
    "DeliveredQuote",
    "InboundMessage",
    "Offering",
    "OfferingStatus",
    "OutboundMessage",
    "Policy",
    "ReplyIntent",
    "RunReport",
    "SendResult",
    "Settings",
    "Unit",
    "make_offering_id",
    "utcnow",
]
