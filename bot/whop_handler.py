from pydantic import BaseModel
from typing import Optional, Dict, Any

class WhopWebhookPayload(BaseModel):
    action: str # e.g., "membership.going_active", "membership.canceled"
    data: Dict[str, Any]

def process_webhook(payload: WhopWebhookPayload, db_session):
    action = payload.action
    data = payload.data
    
    # Very basic MVP processing
    whop_user_id = data.get("user", {}).get("id")
    
    if not whop_user_id:
        return {"status": "error", "message": "No user ID found"}

    from database import User, AuditLog
    from datetime import datetime, timezone
    
    user = db_session.query(User).filter(User.whop_id == whop_user_id).first()
    
    if action == "membership.going_active":
        if not user:
            user = User(whop_id=whop_user_id, is_active=True)
            db_session.add(user)
        else:
            user.is_active = True
        
        log = AuditLog(
            event_type="VIP_GRANTED",
            description=f"Whop ID {whop_user_id} granted VIP access.",
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        db_session.add(log)
        db_session.commit()
        return {"status": "success", "message": "User activated", "whop_id": whop_user_id}
        
    elif action in ["membership.canceled", "membership.payment_failed"]:
        if user:
            user.is_active = False
            log = AuditLog(
                event_type="VIP_REVOKED",
                description=f"Whop ID {whop_user_id} revoked ({action}).",
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            db_session.add(log)
            db_session.commit()
            return {"status": "success", "message": "User deactivated", "whop_id": whop_user_id}
            
    return {"status": "ignored", "action": action}
