import requests
import os

class ZeroBounceVerifier:
    def __init__(self):
        self.api_key = "your-zerobounce-api-key"  # You'll get this after signup
        self.base_url = "https://api.zerobounce.net/v2"
    
    def verify_email(self, email):
        """Verify if email exists using ZeroBounce API"""
        try:
            # Simple API call
            response = requests.get(
                f"{self.base_url}/validate",
                params={
                    "api_key": self.api_key,
                    "email": email
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                status = result.get("status", "unknown")
                
                # Valid statuses
                if status in ["valid", "catch-all"]:
                    return True, "Email is valid and exists"
                else:
                    return False, "Email does not exist or is invalid"
            else:
                # Fallback to basic Gmail check
                return self.fallback_check(email)
                
        except Exception as e:
            print(f"ZeroBounce API error: {e}")
            return self.fallback_check(email)
    
    def fallback_check(self, email):
        """Fallback if API fails"""
        if self.is_gmail_format(email):
            return True, "Gmail format valid (API unavailable)"
        else:
            return False, "Please use a valid Gmail address"
    
    def is_gmail_format(self, email):
        """Basic Gmail format validation"""
        import re
        pattern = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]gmail[.]com$'
        return re.match(pattern, email.lower()) is not None

# Create global instance
email_verifier = ZeroBounceVerifier()