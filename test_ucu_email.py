# test_ucu_email.py
from app import app, UCUEmail, is_valid_ucu_email

with app.app_context():
    # Test a student email that should exist
    test_emails = [
        "B22134@students.ucu.ac.ug",  # Should exist (Kato David)
        "B22564@students.ucu.ac.ug",  # Should exist (Okello John)
        "okello@ucu.ac.ug",            # Should exist (Dr. Peter Okello)
        "fakeemail@students.ucu.ac.ug", # Should NOT exist
        "B99999@students.ucu.ac.ug",    # Should NOT exist
    ]
    
    print("Testing UCU Email Validation:")
    print("-" * 50)
    
    for email in test_emails:
        is_valid = is_valid_ucu_email(email)
        status = "✅ VALID" if is_valid else "❌ INVALID"
        
        # If valid, get the user details
        if is_valid:
            user = UCUEmail.query.filter_by(email=email, is_active=True).first()
            print(f"{status} - {email}")
            print(f"   Name: {user.full_name}")
            print(f"   Type: {user.user_type}")
            if user.user_type == 'student':
                print(f"   Student #: {user.student_number}")
                print(f"   Year: {user.year_of_study}")
            else:
                print(f"   Title: {user.staff_title}")
            print(f"   Department: {user.department}")
            print(f"   Faculty: {user.faculty}")
        else:
            print(f"{status} - {email}")
        print()