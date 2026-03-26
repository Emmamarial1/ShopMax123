# reset_db.py
from app import app, db
from app import generate_complete_ucu_data

def reset_database():
    with app.app_context():
        print("Dropping all tables...")
        db.drop_all()
        print("✅ Dropped all tables")
        
        print("Creating all tables...")
        db.create_all()
        print("✅ Created all tables with new schema")
        
        print("Populating UCU data...")
        count = generate_complete_ucu_data()
        print(f"✅ Added {count} UCU email records")
        
        print("\n🎉 Database reset complete!")

if __name__ == "__main__":
    reset_database()