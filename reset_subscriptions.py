# reset_subscriptions.py - Run this once to clean everything
from app import app, db, User
from datetime import datetime

def reset_all_subscriptions():
    with app.app_context():
        # Reset ALL sellers to clean state
        sellers = User.query.filter_by(user_type='seller').all()
        
        for seller in sellers:
            seller.subscription_tier = None
            seller.subscription_expiry = None
            print(f"Reset seller: {seller.fullname}")
        
        db.session.commit()
        print(f"✅ Reset {len(sellers)} sellers to clean state")

if __name__ == '__main__':
    reset_all_subscriptions()