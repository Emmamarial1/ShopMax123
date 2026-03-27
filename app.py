from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename  
from flask_migrate import Migrate
from sqlalchemy import func, or_, extract, inspect, desc
from datetime import datetime, timedelta
import os
from io import BytesIO
import csv
import json
import secrets
from flask_cors import CORS
import re
from functools import wraps
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import traceback

# Add these lines to load .env file
from dotenv import load_dotenv
load_dotenv()  # This loads the .env file

# Add these imports for Google OAuth
from authlib.integrations.flask_client import OAuth
import requests

# ==================== APP CONFIGURATION ====================
app = Flask(__name__)

# App Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-here')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Email Configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your-app@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'your-app-password')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', 'your-app@gmail.com')

# Upload Configuration
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB max file size

# ==================== GOOGLE OAUTH CONFIGURATION ====================
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        'prompt': 'select_account'
    }
)

# ==================== DATABASE CONFIGURATION ====================
import os
from urllib.parse import urlparse

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    uri = urlparse(DATABASE_URL)
    if uri.scheme == 'postgres':
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 5,
        'pool_recycle': 300,
        'connect_args': {'sslmode': 'require'}, 
        'pool_pre_ping': True
    }
    print("✅ Using PostgreSQL database (Production)")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shopmax.db'
    print("✅ Using SQLite database (Development)")

# ==================== DATABASE INITIALIZATION ====================
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# FORCE TABLE CREATION
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created/verified!")
        
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        print(f"📊 Tables in database: {tables}")
        
    except Exception as e:
        print(f"⚠️ Database initialization error: {e}")
        import traceback
        traceback.print_exc()


@app.route('/create-tables')
def create_tables():
    """Temporary route to create database tables"""
    try:
        db.create_all()
        return "✅ Database tables created successfully! <a href='/'>Go Home</a>"
    except Exception as e:
        return f"❌ Error: {e}"


# Add Socket.IO right here after db initialization
from flask_cors import CORS

# ==================== DATABASE MODELS ====================

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    location = db.Column(db.String(100), nullable=True)
    password = db.Column(db.String(200), nullable=True)
    user_type = db.Column(db.String(20), nullable=False)
    delivery_address = db.Column(db.Text)
    business_name = db.Column(db.String(100))
    business_address = db.Column(db.Text)
    nin = db.Column(db.String(50))
    subscription_tier = db.Column(db.String(20), default=None)
    subscription_expiry = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    business_type = db.Column(db.String(50))
    business_description = db.Column(db.Text)
    business_phone = db.Column(db.String(20))
    business_email = db.Column(db.String(100))
    profile_image = db.Column(db.String(200), nullable=True)
    seller_rating = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)

    # ============ ADD THESE PROPERTIES RIGHT HERE ============
    @property
    def is_new_seller(self):
        """Check if seller is new (never subscribed before)"""
        # A seller is new if:
        # 1. They are a seller
        # 2. They have no subscription tier (None or empty string)
        # 3. They have no subscription expiry
        if self.user_type != 'seller':
            return False
        
        # Check if they have any subscription
        has_subscription = (self.subscription_tier is not None and 
                           self.subscription_tier != '' and 
                           self.subscription_tier != 'basic' and 
                           self.subscription_tier != 'premium' and
                           self.subscription_tier != 'starter')
        
        # Also check if they have an expiry date
        has_expiry = self.subscription_expiry is not None
        
        # They are new if they have NO subscription tier AND NO expiry
        return (self.subscription_tier is None or self.subscription_tier == '') and self.subscription_expiry is None
    
    @property
    def has_active_subscription(self):
        """Check if seller has an active subscription"""
        if self.user_type != 'seller':
            return True
        
        # Check if they have a subscription tier and expiry that's not expired
        if self.subscription_tier and self.subscription_expiry:
            return self.subscription_expiry > datetime.utcnow()
        
        return False
    
    @property
    def current_subscription(self):
        """Get current subscription details"""
        if self.user_type != 'seller':
            return None
        
        if self.subscription_tier and self.subscription_expiry and self.subscription_expiry > datetime.utcnow():
            return {
                'plan': self.subscription_tier,
                'expires': self.subscription_expiry,
                'is_active': True
            }
        return None
    # ============ END OF PROPERTIES ============

    # Relationships - FIX THESE
    products = db.relationship('Product', backref='seller', lazy=True)
    orders = db.relationship('Order', backref='user', foreign_keys='Order.user_id', lazy=True)
    order_items = db.relationship('OrderItem', backref='seller', foreign_keys='OrderItem.seller_id', lazy=True)
    wishlists = db.relationship('Wishlist', backref='user', lazy=True)
    carts = db.relationship('Cart', backref='user', lazy=True)
    reviews = db.relationship('Review', backref='user', lazy=True)

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    stock = db.Column(db.Integer, default=0)
    image = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)
    brand = db.Column(db.String(50), nullable=True)
    condition = db.Column(db.String(20), default='new', nullable=True)
    weight = db.Column(db.Float, nullable=True)
    dimensions = db.Column(db.String(50), nullable=True)
    tags = db.Column(db.String(200), nullable=True)
    sold_count = db.Column(db.Integer, default=0)
    discount_percent = db.Column(db.Integer, default=0)
    old_price = db.Column(db.Float, nullable=True)
    rating = db.Column(db.Float, default=0.0)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    order_items = db.relationship('OrderItem', backref='product', lazy=True)
    wishlists = db.relationship('Wishlist', backref='product', lazy=True)
    carts = db.relationship('Cart', backref='product', lazy=True)
    reviews = db.relationship('Review', backref='product', lazy=True)
    
    def average_rating(self):
        if self.reviews:
            return sum(review.rating for review in self.reviews) / len(self.reviews)
        return 0

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    delivery_address = db.Column(db.Text)
    payment_method = db.Column(db.String(50))
    payment_status = db.Column(db.String(20), default='pending')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # This should be fine
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    tracking_number = db.Column(db.String(50), unique=True, nullable=True)
    delivery_status = db.Column(db.String(50), default='pending')
    delivery_person_id = db.Column(db.Integer, db.ForeignKey('delivery_persons.id'), nullable=True)
    estimated_delivery = db.Column(db.DateTime, nullable=True)
    actual_delivery = db.Column(db.DateTime, nullable=True)
    delivery_notes = db.Column(db.Text, nullable=True)
    current_location = db.Column(db.String(200), nullable=True)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    order_items = db.relationship('OrderItem', backref='order', lazy=True)

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

class Wishlist(db.Model):
    __tablename__ = 'wishlists'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Cart(db.Model):
    __tablename__ = 'carts'
    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, default=1)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SubscriptionPlan(db.Model):
    __tablename__ = 'subscription_plans'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    tier = db.Column(db.String(20), nullable=False)
    price = db.Column(db.Float, nullable=False)
    duration = db.Column(db.String(20), nullable=False)
    features = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class OrderTracking(db.Model):
    __tablename__ = 'order_tracking'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    order = db.relationship('Order', backref=db.backref('tracking_updates', lazy=True))

class DeliveryPerson(db.Model):
    __tablename__ = 'delivery_persons'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    photo = db.Column(db.String(200), nullable=True)
   
    vehicle_type = db.Column(db.String(50), default='motorcycle')
    vehicle_number = db.Column(db.String(50))
    current_location = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    
    deliveries = db.relationship('Order', backref='delivery_person', lazy=True)

class DeliveryAssignment(db.Model):
    __tablename__ = 'delivery_assignments'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    delivery_person_id = db.Column(db.Integer, db.ForeignKey('delivery_persons.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='assigned')
    
    order = db.relationship('Order', backref=db.backref('delivery_assignments', lazy=True))
    delivery_person = db.relationship('DeliveryPerson', backref=db.backref('assignments', lazy=True))

class DeliveryTracking(db.Model):
    __tablename__ = 'delivery_tracking'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    delivery_person_id = db.Column(db.Integer, db.ForeignKey('delivery_persons.id'), nullable=True)
    latitude = db.Column(db.Float, nullable=True)  # Store actual GPS coordinates
    longitude = db.Column(db.Float, nullable=True)
    location_name = db.Column(db.String(200), nullable=True)  # Human-readable address
    status = db.Column(db.String(50), nullable=False)
    battery_level = db.Column(db.Integer, nullable=True)
    speed = db.Column(db.Float, nullable=True)  # Speed in km/h
    accuracy = db.Column(db.Float, nullable=True)  # GPS accuracy in meters
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    order = db.relationship('Order', backref=db.backref('tracking_points', lazy=True))
    delivery_person = db.relationship('DeliveryPerson', backref=db.backref('tracking_points', lazy=True))

class PasswordReset(db.Model):
    __tablename__ = 'password_resets'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)

class EmailVerification(db.Model):
    __tablename__ = 'email_verifications'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_verified = db.Column(db.Boolean, default=False)

class UCUEmail(db.Model):
    __tablename__ = 'ucu_emails'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    user_type = db.Column(db.String(20), nullable=False)
    student_number = db.Column(db.String(10), nullable=True)
    department = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    year_of_study = db.Column(db.Integer, nullable=True)
    faculty = db.Column(db.String(100), nullable=True)
    staff_title = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<UCUEmail {self.email}>'

class NINVerification(db.Model):
    __tablename__ = 'nin_verifications'
    id = db.Column(db.Integer, primary_key=True)
    nin = db.Column(db.String(50), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    date_of_birth = db.Column(db.String(20))
    is_valid = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



class DeliveryProof(db.Model):
    __tablename__ = 'delivery_proofs'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    delivery_person_id = db.Column(db.Integer, db.ForeignKey('delivery_persons.id'), nullable=False)
    photo = db.Column(db.String(200), nullable=True)
    signature = db.Column(db.Text, nullable=True)
    recipient_name = db.Column(db.String(100), nullable=True)
    recipient_phone = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    order = db.relationship('Order', backref=db.backref('delivery_proof', uselist=False))
    delivery_person = db.relationship('DeliveryPerson', backref=db.backref('delivery_proofs', lazy=True))

class DeliveryCheckpoint(db.Model):
    __tablename__ = 'delivery_checkpoints'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    checkpoint_type = db.Column(db.String(50), nullable=False)  # warehouse, pickup, transit, destination
    location = db.Column(db.String(200), nullable=True)
    estimated_arrival = db.Column(db.DateTime, nullable=True)
    actual_arrival = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    order = db.relationship('Order', backref=db.backref('checkpoints', lazy=True))


class Issue(db.Model):
    __tablename__ = 'issues'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    issue_type = db.Column(db.String(50), nullable=False)  # 'damaged', 'wrong_item', 'missing', 'late', 'other'
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')  # 'pending', 'in_progress', 'resolved', 'closed'
    priority = db.Column(db.String(20), default='medium')  # 'low', 'medium', 'high', 'urgent'
    action_taken = db.Column(db.Text, nullable=True)
    resolution_notes = db.Column(db.Text, nullable=True)
    admin_response = db.Column(db.Text, nullable=True)
    responded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    responded_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    order = db.relationship('Order', backref=db.backref('issues', lazy=True))
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('reported_issues', lazy=True))
    responder = db.relationship('User', foreign_keys=[responded_by], backref=db.backref('responded_issues', lazy=True))
    
    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'issue_type': self.issue_type,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'action_taken': self.action_taken,
            'resolution_notes': self.resolution_notes,
            'admin_response': self.admin_response,
            'responder_name': self.responder.fullname if self.responder else None,
            'responded_at': self.responded_at.strftime('%d %b %Y %H:%M') if self.responded_at else None,
            'created_at': self.created_at.strftime('%d %b %Y %H:%M'),
            'order_total': self.order.total_amount if self.order else 0
        }

class IssueMessage(db.Model):
    __tablename__ = 'issue_messages'
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_admin_reply = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    issue = db.relationship('Issue', backref=db.backref('messages', lazy=True))
    sender = db.relationship('User', foreign_keys=[sender_id], backref=db.backref('issue_messages', lazy=True))


# ==================== CHAT MODELS ====================
# Add these after your existing models (around line 300-400)

class Conversation(db.Model):
    __tablename__ = 'conversations'
    id = db.Column(db.Integer, primary_key=True)
    participant1_id = db.Column(db.Integer, nullable=False)
    participant1_type = db.Column(db.String(20), nullable=False)  # 'buyer', 'seller', 'admin'
    participant2_id = db.Column(db.Integer, nullable=False)
    participant2_type = db.Column(db.String(20), nullable=False)
    conversation_type = db.Column(db.String(20), nullable=False)  # 'buyer-seller', 'seller-admin', 'buyer-admin'
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    last_message_id = db.Column(db.Integer, nullable=True)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    
    # Relationships
    messages = db.relationship('Message', backref='conversation', lazy=True, cascade='all, delete-orphan')
    product = db.relationship('Product', backref='conversations')
    order = db.relationship('Order', backref='conversations')

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False)
    sender_id = db.Column(db.Integer, nullable=False)
    sender_type = db.Column(db.String(20), nullable=False)  # 'buyer', 'seller', 'admin'
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default='text')  # 'text', 'image', 'file'
    attachments = db.Column(db.Text, nullable=True)  # JSON string of attachments
    is_read = db.Column(db.Boolean, default=False)
    is_delivered = db.Column(db.Boolean, default=False)
    is_edited = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    reply_to_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'sender_id': self.sender_id,
            'sender_type': self.sender_type,
            'content': self.content,
            'message_type': self.message_type,
            'attachments': json.loads(self.attachments) if self.attachments else [],
            'is_read': self.is_read,
            'is_delivered': self.is_delivered,
            'is_edited': self.is_edited,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class MessageReadReceipt(db.Model):
    __tablename__ = 'message_read_receipts'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    user_type = db.Column(db.String(20), nullable=False)
    read_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    message = db.relationship('Message', backref='read_receipts')





# ==================== PRODUCTION SETTINGS ====================

# For Render.com - ensure upload folder exists
if not os.path.exists('static/uploads'):
    os.makedirs('static/uploads')
    os.makedirs('static/uploads/payments')
    os.makedirs('static/uploads/products')

# Disable AI features on cloud (Ollama won't work)
if os.environ.get('RENDER'):
    print("⚠️ Running on Render - AI chatbot disabled (uses local Ollama only)")
    # You can add a simple rule-based chatbot for cloud



# ==================== HELPER FUNCTIONS ====================


# Add this near the top of your app.py after app configuration
def ensure_upload_folder():
    """Ensure upload folders exist"""
    folders = [
        app.config['UPLOAD_FOLDER'],
        os.path.join(app.config['UPLOAD_FOLDER'], 'payments'),
        os.path.join(app.config['UPLOAD_FOLDER'], 'products')
    ]
    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"Created folder: {folder}")

# Call this after app configuration
ensure_upload_folder()




def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ensure_upload_folder():
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

def get_current_user():
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])  # Fixed: Updated to modern syntax
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'danger')
            return redirect(url_for('login'))
        
        user = get_current_user()
        if not user or user.user_type != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def is_admin():
    user = get_current_user()
    return user and user.user_type == 'admin'

def has_active_subscription(user):
    if user.user_type != 'seller':
        return True
    
    if user.subscription_tier == 'basic' and user.subscription_expiry is None:
        return False
    
    if user.subscription_expiry and user.subscription_expiry < datetime.utcnow():
        return False
    
    return True

def is_valid_email(email):
    if not email:
        return False
    
    email = email.lower().strip()
    
    if email.endswith('@gmail.com'):
        gmail_pattern = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]gmail[.]com$'
        return re.match(gmail_pattern, email) is not None
    
    if email.endswith('@students.ucu.ac.ug'):
        ucu_pattern = r'^[ab][0-9]{5}[@]students[.]ucu[.]ac[.]ug$'
        return re.match(ucu_pattern, email) is not None
    
    if email.endswith('@ucu.ac.ug'):
        staff_pattern = r'^[a-z]+@ucu\.ac\.ug$'
        return re.match(staff_pattern, email) is not None
    
    return False

def get_user_type_by_email(email):
    email = email.lower().strip()
    if email.endswith('@students.ucu.ac.ug'):
        return 'ucu_student'
    elif email.endswith('@ucu.ac.ug'):
        return 'ucu_staff'
    return 'regular_user'

def merge_session_cart_with_user(user_id):
    if 'cart' in session and session['cart']:
        try:
            for product_id_str, quantity in session['cart'].items():
                try:
                    product_id = int(product_id_str)
                except ValueError:
                    continue
                
                product = Product.query.filter_by(id=product_id, is_active=True).first()
                if not product:
                    continue
                
                existing_cart_item = Cart.query.filter_by(
                    user_id=user_id, 
                    product_id=product_id
                ).first()
                
                if existing_cart_item:
                    new_quantity = existing_cart_item.quantity + quantity
                    if new_quantity > product.stock:
                        new_quantity = product.stock
                    existing_cart_item.quantity = new_quantity
                else:
                    final_quantity = min(quantity, product.stock)
                    cart_item = Cart(
                        user_id=user_id, 
                        product_id=product_id, 
                        quantity=final_quantity
                    )
                    db.session.add(cart_item)
            
            db.session.commit()
            session.pop('cart', None)
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Error merging cart: {e}")
            return False
    return True

def calculate_percentage_change(current, previous):
    if previous == 0:
        return 0 if current == 0 else 100
    return round(((current - previous) / previous) * 100, 1)

def get_wishlist_ids(user_id):
    wishlist_items = Wishlist.query.filter_by(user_id=user_id).all()
    return [item.product_id for item in wishlist_items]





# Add this helper function to your app.py
def get_order_status_display(status):
    """Convert database status to user-friendly display text"""
    status_map = {
        'pending': 'Pending',
        'confirmed': 'Confirmed',
        'processing': 'Processing',
        'shipped': 'Out for Delivery',  # This is the key change
        'out_for_delivery': 'Out for Delivery',
        'in_transit': 'Out for Delivery',
        'delivered': 'Delivered',
        'completed': 'Completed',
        'cancelled': 'Cancelled'
    }
    return status_map.get(status, status.replace('_', ' ').title())


# ==================== CHAT HELPER FUNCTIONS ====================

def get_user_conversations(user_id, user_type, page=1, limit=20):
    """Get all conversations for a user"""
    try:
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == user_id, Conversation.participant1_type == user_type),
                db.and_(Conversation.participant2_id == user_id, Conversation.participant2_type == user_type)
            ),
            Conversation.is_active == True
        ).order_by(Conversation.last_message_at.desc()).paginate(page=page, per_page=limit, error_out=False)
        
        result = []
        for conv in conversations.items:
            # Get other participant
            if conv.participant1_id == user_id and conv.participant1_type == user_type:
                other_id = conv.participant2_id
                other_type = conv.participant2_type
            else:
                other_id = conv.participant1_id
                other_type = conv.participant1_type
            
            # Get other participant details
            other_user = None
            if other_type == 'buyer':
                other_user = User.query.get(other_id)
            elif other_type == 'seller':
                other_user = User.query.get(other_id)
            elif other_type == 'admin':
                other_user = User.query.get(other_id)
            
            # Get last message
            last_message = Message.query.filter_by(conversation_id=conv.id).order_by(Message.created_at.desc()).first()
            
            # Get unread count
            unread_count = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != user_id,
                Message.sender_type != user_type,
                Message.is_read == False
            ).count()
            
            result.append({
                'id': conv.id,
                'type': conv.conversation_type,
                'other_participant': {
                    'id': other_id,
                    'name': other_user.fullname if other_user else 'Unknown',
                    'type': other_type,
                    'avatar': other_user.profile_image if other_user else None
                },
                'last_message': last_message.to_dict() if last_message else None,
                'unread_count': unread_count,
                'product_id': conv.product_id,
                'order_id': conv.order_id,
                'created_at': conv.created_at.isoformat() if conv.created_at else None,
                'updated_at': conv.updated_at.isoformat() if conv.updated_at else None
            })
        
        return result
    except Exception as e:
        print(f"Error in get_user_conversations: {e}")
        return []



def get_conversation_messages(conversation_id, user_id, page=1, limit=50):
    """Get messages for a conversation"""
    try:
        messages = Message.query.filter_by(
            conversation_id=conversation_id,
            is_deleted=False
        ).order_by(Message.created_at.desc()).paginate(page=page, per_page=limit, error_out=False)
        
        return [msg.to_dict() for msg in messages.items]
    except Exception as e:
        print(f"Error in get_conversation_messages: {e}")
        return []

def create_message(conversation_id, sender_id, sender_type, content, attachments=None, message_type='text'):
    """Create a new message"""
    try:
        message = Message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            sender_type=sender_type,
            content=content,
            message_type=message_type,
            attachments=json.dumps(attachments) if attachments else None,
            created_at=datetime.utcnow()
        )
        db.session.add(message)
        
        # Update conversation last message
        conversation = Conversation.query.get(conversation_id)
        if conversation:
            conversation.last_message_id = message.id
            conversation.last_message_at = datetime.utcnow()
        
        db.session.commit()
        
        return message.to_dict()
    except Exception as e:
        db.session.rollback()
        print(f"Error in create_message: {e}")
        return None

def create_conversation(participant1_id, participant1_type, participant2_id, participant2_type, conv_type, product_id=None, order_id=None):
    """Create a new conversation"""
    try:
        # Check if conversation already exists
        existing = Conversation.query.filter(
            db.or_(
                db.and_(
                    Conversation.participant1_id == participant1_id,
                    Conversation.participant1_type == participant1_type,
                    Conversation.participant2_id == participant2_id,
                    Conversation.participant2_type == participant2_type
                ),
                db.and_(
                    Conversation.participant1_id == participant2_id,
                    Conversation.participant1_type == participant2_type,
                    Conversation.participant2_id == participant1_id,
                    Conversation.participant2_type == participant1_type
                )
            ),
            Conversation.conversation_type == conv_type,
            Conversation.is_active == True
        ).first()
        
        if existing:
            return {'id': existing.id, 'exists': True}
        
        conversation = Conversation(
            participant1_id=participant1_id,
            participant1_type=participant1_type,
            participant2_id=participant2_id,
            participant2_type=participant2_type,
            conversation_type=conv_type,
            product_id=product_id,
            order_id=order_id,
            created_at=datetime.utcnow()
        )
        db.session.add(conversation)
        db.session.commit()
        
        return {'id': conversation.id, 'exists': False}
    except Exception as e:
        db.session.rollback()
        print(f"Error in create_conversation: {e}")
        return None

def mark_conversation_as_read(conversation_id, user_id):
    """Mark all messages in a conversation as read"""
    try:
        # Get conversation to determine user type
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return
        
        # Determine user type in this conversation
        if conversation.participant1_id == user_id:
            user_type = conversation.participant1_type
        elif conversation.participant2_id == user_id:
            user_type = conversation.participant2_type
        else:
            return
        
        # Mark messages as read
        messages = Message.query.filter(
            Message.conversation_id == conversation_id,
            Message.sender_id != user_id,
            Message.sender_type != user_type,
            Message.is_read == False
        ).all()
        
        for msg in messages:
            msg.is_read = True
            # Add read receipt
            receipt = MessageReadReceipt(
                message_id=msg.id,
                user_id=user_id,
                user_type=user_type,
                read_at=datetime.utcnow()
            )
            db.session.add(receipt)
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error in mark_conversation_as_read: {e}")

def get_seller_conversations(seller_id, page=1, limit=20):
    """Get all conversations for a seller"""
    try:
        return get_user_conversations(seller_id, 'seller', page, limit)
    except Exception as e:
        print(f"Error in get_seller_conversations: {e}")
        return []

def get_admin_conversations(admin_id, page=1, limit=20):
    """Get all conversations for an admin"""
    try:
        return get_user_conversations(admin_id, 'admin', page, limit)
    except Exception as e:
        print(f"Error in get_admin_conversations: {e}")
        return []

def get_conversation_by_id(conversation_id, user_id):
    """Get conversation details by ID"""
    try:
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return None
        
        # Check if user is part of conversation
        if not (conversation.participant1_id == user_id or conversation.participant2_id == user_id):
            return None
        
        # Get other participant
        if conversation.participant1_id == user_id:
            other_id = conversation.participant2_id
            other_type = conversation.participant2_type
        else:
            other_id = conversation.participant1_id
            other_type = conversation.participant1_type
        
        # Get other user details
        other_user = None
        if other_type == 'buyer':
            other_user = User.query.get(other_id)
        elif other_type == 'seller':
            other_user = User.query.get(other_id)
        elif other_type == 'admin':
            other_user = User.query.get(other_id)
        
        # Get product details if any
        product = None
        if conversation.product_id:
            product = Product.query.get(conversation.product_id)
        
        # Get order details if any
        order = None
        if conversation.order_id:
            order = Order.query.get(conversation.order_id)
        
        return {
            'id': conversation.id,
            'type': conversation.conversation_type,
            'other_participant': {
                'id': other_id,
                'name': other_user.fullname if other_user else 'Unknown',
                'type': other_type,
                'avatar': other_user.profile_image if other_user else None,
                'phone': other_user.phone if other_user else None,
                'email': other_user.email if other_user else None
            },
            'product': {
                'id': product.id,
                'name': product.name,
                'price': product.price,
                'image': product.image
            } if product else None,
            'order': {
                'id': order.id,
                'order_number': order.id,
                'total': order.total_amount,
                'status': order.status
            } if order else None,
            'created_at': conversation.created_at.isoformat() if conversation.created_at else None
        }
    except Exception as e:
        print(f"Error in get_conversation_by_id: {e}")
        return None

def find_existing_conversation(user1_id, user2_id, conv_type):
    """Find existing conversation between two users"""
    try:
        conversation = Conversation.query.filter(
            db.or_(
                db.and_(
                    Conversation.participant1_id == user1_id,
                    Conversation.participant2_id == user2_id
                ),
                db.and_(
                    Conversation.participant1_id == user2_id,
                    Conversation.participant2_id == user1_id
                )
            ),
            Conversation.conversation_type == conv_type,
            Conversation.is_active == True
        ).first()
        
        return {'id': conversation.id} if conversation else None
    except Exception as e:
        print(f"Error in find_existing_conversation: {e}")
        return None

def mark_messages_as_read(conversation_id, message_ids, user_id):
    """Mark specific messages as read"""
    try:
        # Get conversation to determine user type
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return
        
        # Determine user type
        if conversation.participant1_id == user_id:
            user_type = conversation.participant1_type
        elif conversation.participant2_id == user_id:
            user_type = conversation.participant2_type
        else:
            return
        
        messages = Message.query.filter(
            Message.id.in_(message_ids),
            Message.conversation_id == conversation_id,
            Message.sender_id != user_id,
            Message.sender_type != user_type,
            Message.is_read == False
        ).all()
        
        for msg in messages:
            msg.is_read = True
            receipt = MessageReadReceipt(
                message_id=msg.id,
                user_id=user_id,
                user_type=user_type,
                read_at=datetime.utcnow()
            )
            db.session.add(receipt)
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error in mark_messages_as_read: {e}")

def delete_message(message_id, user_id):
    """Delete a message (soft delete)"""
    try:
        message = Message.query.get(message_id)
        if not message:
            return
        
        # Check if user is the sender
        if message.sender_id != user_id:
            return
        
        message.is_deleted = True
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error in delete_message: {e}")

def edit_message(message_id, user_id, new_content):
    """Edit a message"""
    try:
        message = Message.query.get(message_id)
        if not message:
            return None
        
        # Check if user is the sender
        if message.sender_id != user_id:
            return None
        
        message.content = new_content
        message.is_edited = True
        db.session.commit()
        
        return message.to_dict()
    except Exception as e:
        db.session.rollback()
        print(f"Error in edit_message: {e}")
        return None

def search_user_messages(user_id, query):
    """Search messages for a user"""
    try:
        # Get all conversations for user
        conversations = Conversation.query.filter(
            db.or_(
                Conversation.participant1_id == user_id,
                Conversation.participant2_id == user_id
            )
        ).all()
        
        conv_ids = [c.id for c in conversations]
        
        messages = Message.query.filter(
            Message.conversation_id.in_(conv_ids),
            Message.content.ilike(f'%{query}%'),
            Message.is_deleted == False
        ).order_by(Message.created_at.desc()).limit(50).all()
        
        results = []
        for msg in messages:
            # Get conversation to find other participant
            conv = next((c for c in conversations if c.id == msg.conversation_id), None)
            if conv:
                if conv.participant1_id == user_id:
                    other_id = conv.participant2_id
                    other_type = conv.participant2_type
                else:
                    other_id = conv.participant1_id
                    other_type = conv.participant1_type
                
                other_user = User.query.get(other_id)
                
                results.append({
                    'id': msg.id,
                    'conversation_id': msg.conversation_id,
                    'content': msg.content,
                    'created_at': msg.created_at.isoformat(),
                    'sender_id': msg.sender_id,
                    'sender_type': msg.sender_type,
                    'other_participant': {
                        'id': other_id,
                        'name': other_user.fullname if other_user else 'Unknown',
                        'type': other_type
                    }
                })
        
        return results
    except Exception as e:
        print(f"Error in search_user_messages: {e}")
        return []

def get_message_statistics():
    """Get message statistics for admin"""
    try:
        total_conversations = Conversation.query.count()
        total_messages = Message.query.count()
        unread_messages = Message.query.filter_by(is_read=False).count()
        
        today = datetime.utcnow().date()
        active_today = Message.query.filter(
            db.func.date(Message.created_at) == today
        ).count()
        
        return {
            'total_conversations': total_conversations,
            'total_messages': total_messages,
            'unread_messages': unread_messages,
            'active_today': active_today
        }
    except Exception as e:
        print(f"Error in get_message_statistics: {e}")
        return {
            'total_conversations': 0,
            'total_messages': 0,
            'unread_messages': 0,
            'active_today': 0
        }

def emit_new_message(conversation_id, message):
    """Emit new message via socket (placeholder - implement with Flask-SocketIO)"""
    # You can implement Flask-SocketIO later for real-time messaging
    pass



@app.context_processor
def inject_message_count():
    """Inject unread message count into all templates"""
    message_count = 0
    if 'user_id' in session:
        user_id = session['user_id']
        user_type = session['user_type']
        
        try:
            conversations = get_user_conversations(user_id, user_type, page=1, limit=100)
            for conv in conversations:
                message_count += conv.get('unread_count', 0)
        except Exception as e:
            print(f"Error getting message count: {e}")
    
    return dict(message_count=message_count)

@app.context_processor
def inject_message_count():
    """Inject unread message count into all templates"""
    message_count = 0
    if 'user_id' in session:
        user_id = session['user_id']
        user_type = session['user_type']
        
        try:
            conversations = get_user_conversations(user_id, user_type, page=1, limit=100)
            for conv in conversations:
                message_count += conv.get('unread_count', 0)
        except Exception as e:
            print(f"Error getting message count: {e}")
    
    return dict(message_count=message_count)



# ==================== SELLER SUBSCRIPTION ROUTES ====================

@app.route('/seller/subscription')
@login_required
def seller_subscription():
    """Seller subscription plans page"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('This page is for sellers only.', 'danger')
        return redirect(url_for('products'))
    
    is_new = (user.subscription_tier is None or user.subscription_tier == '') and user.subscription_expiry is None
    
    return render_template('seller_subscription.html', 
                         user=user, 
                         now=datetime.utcnow,
                         is_new_seller=is_new)







def is_new_seller(user):
    """Check if seller has never subscribed before"""
    # A seller is considered new if:
    # 1. They have no subscription tier (None or empty)
    # 2. They have no subscription expiry date
    # 3. They have never had any subscription before
    return (user.subscription_tier is None or user.subscription_tier == '') and user.subscription_expiry is None



@app.route('/process_payment/<int:plan_id>', methods=['POST'])
@login_required
def process_payment(plan_id):
    """Process payment and activate subscription"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('This page is for sellers only.', 'danger')
        return redirect(url_for('products'))
    
    # Define plan details
    plans = {
        1: {
            'tier': 'starter',
            'duration_days': 30,
            'price': 0,
            'product_limit': 4,
            'name': 'Starter'
        },
        2: {
            'tier': 'basic',
            'duration_days': 30,
            'price': 30000,
            'product_limit': 25,
            'name': 'Business'
        },
        3: {
            'tier': 'premium',
            'duration_days': 120,
            'price': 100000,
            'product_limit': 100,
            'name': 'Semester'
        }
    }
    
    plan = plans.get(plan_id)
    if not plan:
        flash('Invalid subscription plan.', 'danger')
        return redirect(url_for('seller_subscription'))
    
    # CRITICAL: Check if user can get free plan
    if plan_id == 1:
        if not user.is_new_seller:
            flash('The free trial is only available for new sellers. Please choose a paid plan.', 'warning')
            return redirect(url_for('seller_subscription'))
    
    # For paid plans, handle payment proof
    if plan['price'] > 0:
        if 'payment_proof' not in request.files:
            flash('Please upload payment confirmation screenshot.', 'danger')
            return redirect(url_for('seller_payment', plan_id=plan_id))
        
        file = request.files['payment_proof']
        
        if file.filename == '':
            flash('Please select a file to upload.', 'danger')
            return redirect(url_for('seller_payment', plan_id=plan_id))
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
            proof_filename = f"payment_{user.id}_{plan_id}_{timestamp}{filename}"
            
            payment_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'payments')
            if not os.path.exists(payment_dir):
                os.makedirs(payment_dir)
            
            file.save(os.path.join(payment_dir, proof_filename))
            flash('Payment confirmation received! Your subscription will be activated within 24 hours.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid file type. Please upload JPG, PNG, or PDF.', 'danger')
            return redirect(url_for('seller_payment', plan_id=plan_id))
    
    # For free plan - activate immediately
    try:
        # Update user subscription
        user.subscription_tier = plan['tier']
        user.subscription_expiry = datetime.utcnow() + timedelta(days=plan['duration_days'])
        
        db.session.commit()
        
        flash(f'✅ {plan["name"]} plan activated! You can now list up to {plan["product_limit"]} products.', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error processing payment: {e}")
        traceback.print_exc()
        flash('Error processing payment. Please try again.', 'danger')
        return redirect(url_for('seller_payment', plan_id=plan_id))




@app.route('/force-new-seller-status')
@login_required
def force_new_seller_status():
    """Force current user to be a new seller"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        return "Only sellers can use this"
    
    # Force reset
    user.subscription_tier = None
    user.subscription_expiry = None
    db.session.commit()
    
    return f"""
    <html>
    <body style="padding: 40px;">
        <h1>✅ User {user.fullname} is now a NEW SELLER</h1>
        <p>subscription_tier: None</p>
        <p>subscription_expiry: None</p>
        <p>is_new_seller: {user.is_new_seller}</p>
        <a href="/seller/subscription">Go to Subscription Page</a>
    </body>
    </html>
    """



# ==================== DATABASE CONFIGURATION ====================

import os
from urllib.parse import urlparse

# Database configuration for production vs local
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    # Production (Render.com PostgreSQL)
    # Fix for Render's PostgreSQL URL format
    uri = urlparse(DATABASE_URL)
    if uri.scheme == 'postgres':
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 5,
        'pool_recycle': 300,
        'pool_pre_ping': True
    }
    print("✅ Using PostgreSQL database (Production)")
else:
    # Local development (SQLite)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shopmax.db'
    print("✅ Using SQLite database (Development)")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False




@app.route('/admin/sellers')
@admin_required
def admin_sellers():
    """Admin sellers management"""
    user = get_current_user()
    
    # Get filter parameters
    search = request.args.get('search', '')
    status = request.args.get('status', '')
    verification = request.args.get('verification', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    
    # Build query
    query = User.query.filter_by(user_type='seller')
    
    if search:
        query = query.filter(
            db.or_(
                User.fullname.ilike(f'%{search}%'),
                User.email.ilike(f'%{search}%'),
                User.business_name.ilike(f'%{search}%')
            )
        )
    
    if status == 'active':
        query = query.filter_by(is_active=True)
    elif status == 'inactive':
        query = query.filter_by(is_active=False)
    
    if verification == 'verified':
        query = query.filter(User.nin.isnot(None))
    elif verification == 'unverified':
        query = query.filter(User.nin.is_(None))
    
    # Get paginated results
    pagination = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    sellers = pagination.items
    
    # CALCULATE REVENUE, RATING, AND PRODUCTS FOR EACH SELLER
    for seller in sellers:
        # Count products
        seller.products_count = Product.query.filter_by(seller_id=seller.id, is_active=True).count()
        
        # Calculate revenue from completed orders
        total_revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == seller.id,
            Order.status == 'completed'
        ).scalar() or 0
        seller.total_revenue = total_revenue
        
        # CALCULATE SELLER RATING FROM REVIEWS
        # Get all products by this seller
        seller_products = Product.query.filter_by(seller_id=seller.id).all()
        product_ids = [p.id for p in seller_products]
        
        if product_ids:
            # Get all reviews for these products
            reviews = Review.query.filter(Review.product_id.in_(product_ids)).all()
            if reviews:
                avg_rating = sum(r.rating for r in reviews) / len(reviews)
                seller.seller_rating = round(avg_rating, 1)
            else:
                seller.seller_rating = 0.0
        else:
            seller.seller_rating = 0.0
        
        # Update the seller's rating in database (optional)
        if seller.seller_rating != seller.seller_rating:
            seller.seller_rating = seller.seller_rating
            db.session.commit()
    
    # Get counts for stats
    total_sellers = User.query.filter_by(user_type='seller').count()
    active_sellers = User.query.filter_by(user_type='seller', is_active=True).count()
    inactive_sellers = total_sellers - active_sellers
    
    # Calculate TOTAL REVENUE across ALL SELLERS
    total_revenue_all = db.session.query(
        func.sum(OrderItem.price * OrderItem.quantity)
    ).join(Order).filter(
        Order.status == 'completed'
    ).scalar() or 0
    
    # Calculate total products sold across ALL SELLERS
    total_products_sold = db.session.query(
        func.sum(OrderItem.quantity)
    ).join(Order).filter(
        Order.status == 'completed'
    ).scalar() or 0
    
    # Get top sellers by rating
    top_sellers = []
    all_sellers = User.query.filter_by(user_type='seller').all()
    
    for seller in all_sellers:
        # Calculate revenue
        revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == seller.id,
            Order.status == 'completed'
        ).scalar() or 0
        
        # Calculate rating
        seller_products = Product.query.filter_by(seller_id=seller.id).all()
        product_ids = [p.id for p in seller_products]
        rating = 0.0
        if product_ids:
            reviews = Review.query.filter(Review.product_id.in_(product_ids)).all()
            if reviews:
                rating = sum(r.rating for r in reviews) / len(reviews)
        
        top_sellers.append({
            'id': seller.id,
            'fullname': seller.fullname,
            'seller_rating': round(rating, 1),
            'total_revenue': revenue
        })
    
    # Sort by rating and get top 5
    top_sellers.sort(key=lambda x: x['seller_rating'], reverse=True)
    top_sellers = top_sellers[:5]
    
    return render_template('admin_sellers.html',
                         user=user,
                         sellers=sellers,
                         pagination=pagination,
                         total_sellers=total_sellers,
                         active_sellers=active_sellers,
                         inactive_sellers=inactive_sellers,
                         total_revenue_all=total_revenue_all,
                         total_products_sold=total_products_sold,
                         top_sellers=top_sellers,
                         search=search,
                         status=status,
                         verification=verification,
                         per_page=per_page)




@app.route('/complete-seller-registration', methods=['GET', 'POST'])
def complete_seller_registration():
    """Complete registration for UCU students/staff as sellers"""
    if 'verified_ucu_email' not in session:
        flash('Please verify your UCU email first.', 'warning')
        return redirect(url_for('unified_register'))
    
    email = session['verified_ucu_email']
    ucu_name = session['verified_ucu_name']
    ucu_type = session.get('verified_ucu_type', 'student')
    department = session.get('verified_ucu_dept', '')
    student_number = session.get('verified_ucu_student', '')
    staff_title = session.get('verified_ucu_title', '')
    
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        location = request.form.get('location', '').strip()
        business_name = request.form.get('business_name', '').strip()
        business_address = request.form.get('business_address', '').strip()
        nin = request.form.get('nin', '').strip().upper()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validate phone
        if not phone or not re.match(r'^[0-9]{10}$', phone):
            flash('Please enter a valid 10-digit phone number (e.g., 0755123456).', 'danger')
            return render_template('complete_seller_registration.html', 
                                 email=email, name=ucu_name, ucu_type=ucu_type,
                                 department=department, student_number=student_number,
                                 staff_title=staff_title)
        
        # Validate business name
        if not business_name:
            flash('Business name is required for seller accounts.', 'danger')
            return render_template('complete_seller_registration.html', 
                                 email=email, name=ucu_name, ucu_type=ucu_type,
                                 department=department, student_number=student_number,
                                 staff_title=staff_title)
        
        # Validate NIN
        if not nin:
            flash('NIN is required for seller accounts.', 'danger')
            return render_template('complete_seller_registration.html', 
                                 email=email, name=ucu_name, ucu_type=ucu_type,
                                 department=department, student_number=student_number,
                                 staff_title=staff_title)
        
        # Check NIN in database
        nin_record = NINVerification.query.filter(
            func.upper(NINVerification.nin) == nin,
            NINVerification.is_valid == True
        ).first()
        
        if not nin_record:
            available_nins = NINVerification.query.filter_by(is_valid=True).limit(5).all()
            nin_list = ", ".join([n.nin for n in available_nins]) if available_nins else "CM123456789AB, CF987654321CD"
            flash(f'❌ Invalid NIN. Please use one of: {nin_list}', 'danger')
            return render_template('complete_seller_registration.html', 
                                 email=email, name=ucu_name, ucu_type=ucu_type,
                                 department=department, student_number=student_number,
                                 staff_title=staff_title)
        
        # Validate password
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('complete_seller_registration.html', 
                                 email=email, name=ucu_name, ucu_type=ucu_type,
                                 department=department, student_number=student_number,
                                 staff_title=staff_title)
        
        if password and len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('complete_seller_registration.html', 
                                 email=email, name=ucu_name, ucu_type=ucu_type,
                                 department=department, student_number=student_number,
                                 staff_title=staff_title)
        
        try:
            # Check if user already exists
            if User.query.filter_by(email=email).first():
                flash('❌ This email is already registered. Please login instead.', 'warning')
                return redirect(url_for('login'))
            
            # If no password provided, generate one
            show_temp_password = False
            if not password:
                password = secrets.token_urlsafe(8)
                show_temp_password = True
            
            # CRITICAL: Create new user with subscription_tier = None
            new_user = User(
                fullname=ucu_name,
                email=email,
                phone=phone,
                location=location,
                business_name=business_name,
                business_address=business_address or location,
                nin=nin,
                password=generate_password_hash(password),
                user_type='seller',
                subscription_tier=None,      # <-- IMPORTANT: MUST BE None
                subscription_expiry=None,     # <-- IMPORTANT: MUST BE None
                is_active=True
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # Clear session
            session_keys = ['verified_ucu_email', 'verified_ucu_name', 'verified_ucu_type', 
                          'verified_ucu_dept', 'verified_ucu_faculty', 'verified_ucu_student',
                          'verified_ucu_year', 'verified_ucu_title']
            for key in session_keys:
                session.pop(key, None)
            
            # Log the user in
            session['user_id'] = new_user.id
            session['user_name'] = new_user.fullname
            session['user_type'] = new_user.user_type
            
            user_type_display = 'staff' if ucu_type == 'staff' else 'student'
            flash(f'✅ Welcome to ShopMax, {ucu_name}! Your UCU {user_type_display} seller account has been created.', 'success')
            
            if show_temp_password:
                flash(f'🔑 Your temporary password is: {password}. Please change it in your profile.', 'info')
            
            return redirect(url_for('seller_subscription'))
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error creating seller account: {e}")
            traceback.print_exc()
            flash(f'Error creating account: {str(e)}', 'danger')
    
    # GET request - show form
    return render_template('complete_seller_registration.html', 
                         email=email, 
                         name=ucu_name, 
                         ucu_type=ucu_type,
                         department=department,
                         student_number=student_number,
                         staff_title=staff_title)



@app.route('/api/admin/issues/<int:issue_id>/respond', methods=['POST'])
@admin_required
def respond_to_issue(issue_id):
    """Admin responds to an issue - automatically marks as resolved"""
    try:
        data = request.get_json()
        admin_id = session['user_id']
        
        print(f"📝 Responding to issue {issue_id} with data: {data}")
        
        issue = Issue.query.get_or_404(issue_id)
        response_message = data.get('message', '').strip()
        mark_resolved = data.get('mark_resolved', True)  # Default to True
        
        if not response_message:
            return jsonify({'success': False, 'message': 'Response message required'}), 400
        
        # Add admin response message
        admin_message = IssueMessage(
            issue_id=issue_id,
            sender_id=admin_id,
            message=response_message,
            is_admin_reply=True,
            created_at=datetime.utcnow()
        )
        db.session.add(admin_message)
        
        # Update issue with response info
        issue.admin_response = response_message
        issue.responded_by = admin_id
        issue.responded_at = datetime.utcnow()
        issue.updated_at = datetime.utcnow()
        
        # Mark as resolved if requested (default is True)
        if mark_resolved:
            issue.status = 'resolved'
            issue.resolved_at = datetime.utcnow()
            print(f"   ✅ Issue marked as resolved")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Response sent successfully',
            'issue_status': issue.status
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error responding to issue: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/admin/issues')
@admin_required
def admin_issues():
    """Admin page to view all issues"""
    user = get_current_user()
    
    # Get filter parameters
    status = request.args.get('status', '')
    priority = request.args.get('priority', '')
    search = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Build query with eager loading
    query = Issue.query.options(
        db.joinedload(Issue.order),
        db.joinedload(Issue.user),
        db.joinedload(Issue.responder)
    )
    
    if status:
        query = query.filter(Issue.status == status)
    if priority:
        query = query.filter(Issue.priority == priority)
    if search:
        query = query.filter(
            db.or_(
                Issue.description.ilike(f'%{search}%'),
                Issue.id.cast(db.String).ilike(f'%{search}%'),
                Issue.order_id.cast(db.String).ilike(f'%{search}%')
            )
        )
    
    pagination = query.order_by(Issue.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    issues = pagination.items
    
    # Get counts for stats
    # Open issues: pending or in_progress (not resolved or closed)
    open_count = Issue.query.filter(Issue.status.in_(['pending', 'in_progress'])).count()
    
    # Resolved issues
    resolved_count = Issue.query.filter_by(status='resolved').count()
    
    # Urgent priority issues
    urgent_count = Issue.query.filter_by(priority='urgent').count()
    
    # Total issues
    total_issues = Issue.query.count()
    
    print(f"📊 Admin Issues Stats:")
    print(f"   Open Issues: {open_count}")
    print(f"   Resolved: {resolved_count}")
    print(f"   Urgent: {urgent_count}")
    print(f"   Total: {total_issues}")
    
    return render_template('admin_issues.html',
                         user=user,
                         issues=issues,
                         pagination=pagination,
                         pending_count=open_count,
                         in_progress_count=0,
                         resolved_count=resolved_count,
                         urgent_count=urgent_count,
                         total_issues=total_issues)




@app.route('/api/messages/start-admin-chat', methods=['POST'])
@login_required
def api_start_admin_chat():
    """Start a chat with admin (for buyers and sellers)"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        user = get_current_user()
        
        # Get user type from session (works for both buyers and sellers)
        user_type = session.get('user_type', user.user_type if user else 'buyer')
        
        initial_message = data.get('initial_message', 'Hello, I need assistance with ShopMax.')
        
        print(f"🔍 Starting admin chat - User: {user_id}, Type: {user_type}")
        
        # Find an admin user
        admin = User.query.filter_by(user_type='admin').first()
        
        if not admin:
            print("❌ No admin found in database!")
            return jsonify({'success': False, 'error': 'No admin available. Please contact support.'}), 404
        
        print(f"✅ Found admin: {admin.fullname} (ID: {admin.id})")
        
        # Determine conversation type based on user type
        conv_type = f'{user_type}-admin'
        
        # Check if conversation already exists
        existing = Conversation.query.filter(
            db.or_(
                db.and_(
                    Conversation.participant1_id == user_id,
                    Conversation.participant1_type == user_type,
                    Conversation.participant2_id == admin.id,
                    Conversation.participant2_type == 'admin'
                ),
                db.and_(
                    Conversation.participant1_id == admin.id,
                    Conversation.participant1_type == 'admin',
                    Conversation.participant2_id == user_id,
                    Conversation.participant2_type == user_type
                )
            ),
            Conversation.conversation_type == conv_type,
            Conversation.is_active == True
        ).first()
        
        if existing:
            conversation_id = existing.id
            print(f"✅ Found existing admin chat: {conversation_id}")
        else:
            # Create new conversation
            conversation = Conversation(
                participant1_id=user_id,
                participant1_type=user_type,
                participant2_id=admin.id,
                participant2_type='admin',
                conversation_type=conv_type,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(conversation)
            db.session.flush()
            conversation_id = conversation.id
            print(f"✅ Created new admin chat: {conversation_id}")
        
        # Send initial message
        if initial_message:
            message = Message(
                conversation_id=conversation_id,
                sender_id=user_id,
                sender_type=user_type,
                content=initial_message,
                message_type='text',
                created_at=datetime.utcnow()
            )
            db.session.add(message)
            print(f"✅ Added initial message: {initial_message[:50]}...")
            
            # Update conversation
            conv = Conversation.query.get(conversation_id)
            if conv:
                conv.last_message_id = message.id
                conv.last_message_at = datetime.utcnow()
                conv.updated_at = datetime.utcnow()
        
        db.session.commit()
        print(f"✅ Admin chat created successfully with ID: {conversation_id}")
        
        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'message': 'Admin chat started successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in api_start_admin_chat: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/debug/user-relations/<int:user_id>')
@admin_required
def debug_user_relations(user_id):
    """Debug route to see all relationships for a user"""
    try:
        user = User.query.get_or_404(user_id)
        
        html = f"<h1>Debugging User: {user.fullname} (ID: {user.id})</h1>"
        
        # Check all possible relationships
        issues_count = Issue.query.filter_by(user_id=user.id).count()
        html += f"<p>Issues reported: {issues_count}</p>"
        
        issue_messages_count = IssueMessage.query.filter_by(sender_id=user.id).count()
        html += f"<p>Issue messages: {issue_messages_count}</p>"
        
        reviews_count = Review.query.filter_by(user_id=user.id).count()
        html += f"<p>Reviews: {reviews_count}</p>"
        
        wishlist_count = Wishlist.query.filter_by(user_id=user.id).count()
        html += f"<p>Wishlist items: {wishlist_count}</p>"
        
        cart_count = Cart.query.filter_by(user_id=user.id).count()
        html += f"<p>Cart items: {cart_count}</p>"
        
        orders_count = Order.query.filter_by(user_id=user.id).count()
        html += f"<p>Orders: {orders_count}</p>"
        
        # For sellers
        if user.user_type == 'seller':
            products_count = Product.query.filter_by(seller_id=user.id).count()
            html += f"<p>Products: {products_count}</p>"
            
            order_items_count = OrderItem.query.filter_by(seller_id=user.id).count()
            html += f"<p>Order items as seller: {order_items_count}</p>"
        
        # Conversations
        conv_count = Conversation.query.filter(
            db.or_(
                Conversation.participant1_id == user.id,
                Conversation.participant2_id == user.id
            )
        ).count()
        html += f"<p>Conversations: {conv_count}</p>"
        
        messages_count = Message.query.filter_by(sender_id=user.id).count()
        html += f"<p>Messages: {messages_count}</p>"
        
        # Password resets
        reset_count = PasswordReset.query.filter_by(email=user.email).count()
        html += f"<p>Password resets: {reset_count}</p>"
        
        html += "<h2>Foreign Key Constraint Check</h2>"
        
        # Try to get the actual SQLite foreign key error
        try:
            db.session.delete(user)
            db.session.flush()  # This will raise the error without committing
            db.session.rollback()  # Rollback to keep the user
            html += "<p style='color:green'>✅ No foreign key constraints found! User can be deleted.</p>"
        except Exception as e:
            html += f"<p style='color:red'>❌ Error: {str(e)}</p>"
            db.session.rollback()
        
        html += f'<p><a href="/admin/users">Back to Users</a></p>'
        return html
        
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"




@app.route('/seller/plans')
@login_required
def subscription_plans():
    """New subscription plans page"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('This page is for sellers only.', 'danger')
        return redirect(url_for('products'))
    
    # Calculate if user is new seller
    is_new = (user.subscription_tier is None or user.subscription_tier == '') and user.subscription_expiry is None
    
    # Get current subscription if any
    current_subscription = None
    if user.subscription_tier and user.subscription_expiry and user.subscription_expiry > datetime.utcnow():
        current_subscription = {
            'plan': user.subscription_tier,
            'expires': user.subscription_expiry,
            'is_active': True
        }
    
    return render_template('subscription_plans.html', 
                         user=user,
                         current_subscription=current_subscription,
                         has_free_trial=is_new)  # Use is_new for free trial availability

@app.route('/activate/free-trial')
@login_required
def activate_free_trial():
    """Activate free trial for new sellers"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('products'))
    
    # Check if free trial is available
    if user.subscription_tier is not None or user.subscription_tier != '':
        flash('Free trial is only available for new sellers.', 'warning')
        return redirect(url_for('subscription_plans'))
    
    if user.subscription_expiry is not None:
        flash('You have already used your free trial.', 'warning')
        return redirect(url_for('subscription_plans'))
    
    try:
        # Activate free trial
        user.subscription_tier = 'starter'
        user.subscription_expiry = datetime.utcnow() + timedelta(days=30)
        db.session.commit()
        
        flash('✅ Free trial activated! You now have 30 days to sell for free.', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error activating free trial: {e}")
        flash('Error activating free trial. Please try again.', 'danger')
        return redirect(url_for('subscription_plans'))


@app.route('/activate/basic-plan')
@login_required
def activate_basic_plan():
    """Redirect to payment for basic plan"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('products'))
    
    # Store plan selection in session
    session['selected_plan'] = 'basic'
    return redirect(url_for('payment_page'))


@app.route('/activate/premium-plan')
@login_required
def activate_premium_plan():
    """Redirect to payment for premium plan"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('products'))
    
    # Store plan selection in session
    session['selected_plan'] = 'premium'
    return redirect(url_for('payment_page'))


@app.route('/payment-page')
@login_required
def payment_page():
    """Payment page for subscription"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('products'))
    
    plan = session.get('selected_plan', 'basic')
    
    plans = {
        'basic': {
            'name': 'Business Plan',
            'price': 30000,
            'duration': '1 month',
            'product_limit': 25
        },
        'premium': {
            'name': 'Semester Plan',
            'price': 100000,
            'duration': '4 months',
            'product_limit': 100
        }
    }
    
    return render_template('payment_page.html', 
                         user=user, 
                         plan=plans[plan],
                         plan_type=plan)


@app.route('/process-subscription-payment', methods=['POST'])
@login_required
def process_subscription_payment():
    """Process subscription payment"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied.', 'danger')
        return redirect(url_for('products'))
    
    plan_type = request.form.get('plan_type')
    payment_proof = request.files.get('payment_proof')
    
    if not plan_type:
        flash('Invalid plan selection.', 'danger')
        return redirect(url_for('subscription_plans'))
    
    if not payment_proof:
        flash('Please upload payment proof.', 'danger')
        return redirect(url_for('payment_page'))
    
    # Save payment proof
    filename = secure_filename(payment_proof.filename)
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    proof_filename = f"payment_{user.id}_{plan_type}_{timestamp}_{filename}"
    
    payment_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'payments')
    if not os.path.exists(payment_dir):
        os.makedirs(payment_dir)
    
    payment_proof.save(os.path.join(payment_dir, proof_filename))
    
    # For now, store in session and show pending message
    # In production, you'd have admin approval process
    session['pending_subscription'] = {
        'plan': plan_type,
        'proof': proof_filename,
        'submitted_at': datetime.utcnow().isoformat()
    }
    
    flash('✅ Payment proof submitted! Your subscription will be activated within 24 hours after verification.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/admin/verify-payments')
@admin_required
def verify_payments():
    """Admin page to verify subscription payments"""
    # This would show all pending payments for admin approval
    # Implementation depends on your admin panel structure
    pass




@app.route('/seller/payment/<int:plan_id>')
@login_required
def seller_payment(plan_id):
    """Show payment page for selected plan"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('This page is for sellers only.', 'danger')
        return redirect(url_for('products'))
    
    # Define plans - ALL PLANS ALWAYS AVAILABLE
    plans = {
        1: {
            'id': 1,
            'name': 'Starter Plan',
            'tier': 'starter',
            'price': 0,
            'product_limit': 4,
            'duration': '1 month',
        },
        2: {
            'id': 2,
            'name': 'Business Plan',
            'tier': 'basic',
            'price': 30000,
            'product_limit': 25,
            'duration': 'monthly',
        },
        3: {
            'id': 3,
            'name': 'Semester Plan',
            'tier': 'premium',
            'price': 100000,
            'product_limit': 100,
            'duration': '4 months',
        }
    }
    
    plan = plans.get(plan_id)
    if not plan:
        flash('Invalid subscription plan.', 'danger')
        return redirect(url_for('seller_subscription'))
    
    # No restrictions - all plans are available to all sellers
    return render_template('seller_payment.html', plan=plan, user=user)








@app.route('/api/admin/reports/export-all', methods=['GET'])
@admin_required
def export_all_reports():
    """Export all reports in a single ZIP file"""
    try:
        import zipfile
        from io import BytesIO
        from datetime import datetime
        
        # Create a BytesIO object for the ZIP file
        zip_buffer = BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            
            # 1. Sales Report
            sales_data = generate_sales_report_data()
            if sales_data:
                zip_file.writestr(f'sales_report_{timestamp}.csv', sales_data)
            
            # 2. Users Report
            users_data = generate_users_report_data()
            if users_data:
                zip_file.writestr(f'users_report_{timestamp}.csv', users_data)
            
            # 3. Products Report
            products_data = generate_products_report_data()
            if products_data:
                zip_file.writestr(f'products_report_{timestamp}.csv', products_data)
            
            # 4. Sellers Report
            sellers_data = generate_sellers_report_data()
            if sellers_data:
                zip_file.writestr(f'sellers_report_{timestamp}.csv', sellers_data)
            
            # 5. Orders Summary
            orders_summary = generate_orders_summary_data()
            if orders_summary:
                zip_file.writestr(f'orders_summary_{timestamp}.csv', orders_summary)
        
        zip_buffer.seek(0)
        
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'all_reports_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.zip'
        )
        
    except Exception as e:
        print(f"Error exporting all reports: {e}")
        traceback.print_exc()
        flash('Error generating reports', 'danger')
        return redirect(url_for('admin_reports'))

def generate_sales_report_data():
    """Generate sales report CSV data"""
    try:
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Get all completed orders
        orders = Order.query.filter_by(status='completed').order_by(Order.created_at.desc()).all()
        
        writer.writerow(['Order ID', 'Date', 'Customer', 'Amount (UGX)', 'Payment Method', 'Items'])
        
        for order in orders:
            writer.writerow([
                order.id,
                order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else 'N/A',
                order.user.fullname if order.user else 'Guest',
                f"{order.total_amount:,.0f}",
                order.payment_method or 'N/A',
                len(order.order_items)
            ])
        
        total_revenue = sum(o.total_amount for o in orders)
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Orders', len(orders)])
        writer.writerow(['Total Revenue', f'UGX {total_revenue:,.0f}'])
        
        return output.getvalue()
        
    except Exception as e:
        print(f"Error generating sales report data: {e}")
        return None

def generate_users_report_data():
    """Generate users report CSV data"""
    try:
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        users = User.query.order_by(User.created_at.desc()).all()
        
        writer.writerow(['User ID', 'Name', 'Email', 'Type', 'Phone', 'Status', 'Joined Date'])
        
        for user in users:
            writer.writerow([
                user.id,
                user.fullname,
                user.email,
                user.user_type.title() if user.user_type else 'Buyer',
                user.phone or 'N/A',
                'Active' if user.is_active else 'Inactive',
                user.created_at.strftime('%Y-%m-%d') if user.created_at else 'N/A'
            ])
        
        return output.getvalue()
        
    except Exception as e:
        print(f"Error generating users report data: {e}")
        return None

def generate_products_report_data():
    """Generate products report CSV data"""
    try:
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        products = Product.query.order_by(Product.created_at.desc()).all()
        
        writer.writerow(['Product ID', 'Name', 'Seller', 'Category', 'Price (UGX)', 'Stock', 'Sold', 'Status'])
        
        for product in products:
            writer.writerow([
                product.id,
                product.name,
                product.seller.business_name or product.seller.fullname if product.seller else 'N/A',
                product.category.title() if product.category else 'General',
                f"{product.price:,.0f}",
                product.stock,
                product.sold_count or 0,
                'Active' if product.is_active else 'Inactive'
            ])
        
        return output.getvalue()
        
    except Exception as e:
        print(f"Error generating products report data: {e}")
        return None

def generate_sellers_report_data():
    """Generate sellers report CSV data"""
    try:
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        sellers = User.query.filter_by(user_type='seller').order_by(User.created_at.desc()).all()
        
        writer.writerow(['Seller ID', 'Name', 'Email', 'Business', 'Phone', 'Products', 'Revenue', 'Rating', 'Status'])
        
        for seller in sellers:
            products_count = Product.query.filter_by(seller_id=seller.id).count()
            revenue = db.session.query(
                func.sum(OrderItem.price * OrderItem.quantity)
            ).join(Order).filter(
                OrderItem.seller_id == seller.id,
                Order.status == 'completed'
            ).scalar() or 0
            
            writer.writerow([
                seller.id,
                seller.fullname,
                seller.email,
                seller.business_name or 'N/A',
                seller.phone or 'N/A',
                products_count,
                f"{revenue:,.0f}",
                seller.seller_rating or 0,
                'Active' if seller.is_active else 'Inactive'
            ])
        
        return output.getvalue()
        
    except Exception as e:
        print(f"Error generating sellers report data: {e}")
        return None

def generate_orders_summary_data():
    """Generate orders summary CSV data"""
    try:
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Get order status counts
        status_counts = db.session.query(
            Order.status, 
            func.count(Order.id).label('count')
        ).group_by(Order.status).all()
        
        writer.writerow(['Order Status', 'Count'])
        
        for status, count in status_counts:
            writer.writerow([status, count])
        
        writer.writerow([])
        writer.writerow(['Additional Metrics'])
        
        # Total completed orders
        total_completed = Order.query.filter_by(status='completed').count()
        writer.writerow(['Total Completed Orders', total_completed])
        
        # Total revenue
        total_revenue = db.session.query(
            func.sum(Order.total_amount)
        ).filter_by(status='completed').scalar() or 0
        writer.writerow(['Total Revenue', f'UGX {total_revenue:,.0f}'])
        
        return output.getvalue() 
    except Exception as e:
        print(f"Error generating orders summary data: {e}")
        return None


@app.route('/api/admin/sellers/add', methods=['POST'])
@admin_required
def admin_add_seller():
    """Add a new seller"""
    try:
        # Get form data
        fullname = request.form.get('fullname', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        business_name = request.form.get('business_name', '').strip()
        nin = request.form.get('nin', '').strip().upper()
        password = request.form.get('password', '').strip()
        
        # Validate required fields
        if not fullname:
            return jsonify({'success': False, 'message': 'Full name is required'}), 400
        if not email:
            return jsonify({'success': False, 'message': 'Email is required'}), 400
        if not business_name:
            return jsonify({'success': False, 'message': 'Business name is required'}), 400
        if not nin:
            return jsonify({'success': False, 'message': 'NIN is required'}), 400
        if not password:
            return jsonify({'success': False, 'message': 'Password is required'}), 400
        
        # Check if email already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return jsonify({'success': False, 'message': 'Email already registered'}), 400
        
        # Validate phone format
        if phone and not re.match(r'^[0-9]{10}$', phone):
            return jsonify({'success': False, 'message': 'Phone must be 10 digits'}), 400
        
        # Validate password length
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
        
        # Check NIN in database (optional - you can remove if not needed)
        nin_record = NINVerification.query.filter_by(nin=nin, is_valid=True).first()
        if not nin_record:
            # Still allow, just warn
            print(f"⚠️ NIN {nin} not found in verification database")
        
        # Create new seller
        new_seller = User(
            fullname=fullname,
            email=email,
            phone=phone if phone else None,
            user_type='seller',
            business_name=business_name,
            nin=nin,
            password=generate_password_hash(password),
            is_active=True,
            subscription_tier='basic',
            created_at=datetime.utcnow()
        )
        
        db.session.add(new_seller)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Seller created successfully',
            'seller': {
                'id': new_seller.id,
                'fullname': new_seller.fullname,
                'email': new_seller.email,
                'business_name': new_seller.business_name
            }
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error adding seller: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500




@app.route('/api/admin/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    """Delete user permanently with all related records - Complete version"""
    try:
        user = User.query.get_or_404(user_id)
        
        # Don't allow deleting yourself
        if user.id == session['user_id']:
            return jsonify({'success': False, 'message': 'You cannot delete your own account'}), 400
        
        user_name = user.fullname
        user_email = user.email
        
        print(f"\n{'='*50}")
        print(f"🗑️ Deleting user: {user_name} (ID: {user_id}, Type: {user.user_type})")
        print(f"{'='*50}")
        
        # ==================== DELETE IN CORRECT ORDER ====================
        
        # 1. Delete issue messages (these reference issues)
        IssueMessage.query.filter_by(sender_id=user.id).delete()
        print("   ✅ Deleted issue messages")
        
        # 2. Delete issues reported by user
        Issue.query.filter_by(user_id=user.id).delete()
        print("   ✅ Deleted reported issues")
        
        # 3. Delete user's reviews
        Review.query.filter_by(user_id=user.id).delete()
        print("   ✅ Deleted reviews")
        
        # 4. Delete user's wishlist items
        Wishlist.query.filter_by(user_id=user.id).delete()
        print("   ✅ Deleted wishlist items")
        
        # 5. Delete user's cart items
        Cart.query.filter_by(user_id=user.id).delete()
        print("   ✅ Deleted cart items")
        
        # 6. Delete messages and conversations
        # Get all conversations where user is participant
        conversations = Conversation.query.filter(
            db.or_(
                Conversation.participant1_id == user.id,
                Conversation.participant2_id == user.id
            )
        ).all()
        
        for conv in conversations:
            # Delete messages in this conversation
            Message.query.filter_by(conversation_id=conv.id).delete()
            # Delete read receipts
            MessageReadReceipt.query.filter_by(user_id=user.id).delete()
            # Delete the conversation
            db.session.delete(conv)
        print(f"   ✅ Deleted {len(conversations)} conversations and messages")
        
        # 7. Delete order items (these reference orders and products)
        if user.user_type == 'seller':
            # For sellers, delete order items where they are the seller
            OrderItem.query.filter_by(seller_id=user.id).delete()
            print("   ✅ Deleted order items as seller")
        
        # 8. Delete orders (for buyers)
        if user.user_type == 'buyer':
            orders = Order.query.filter_by(user_id=user.id).all()
            for order in orders:
                # Delete order tracking
                OrderTracking.query.filter_by(order_id=order.id).delete()
                # Delete delivery assignments
                DeliveryAssignment.query.filter_by(order_id=order.id).delete()
                # Delete delivery tracking
                DeliveryTracking.query.filter_by(order_id=order.id).delete()
                # Delete delivery proof
                DeliveryProof.query.filter_by(order_id=order.id).delete()
                # Delete delivery checkpoints
                DeliveryCheckpoint.query.filter_by(order_id=order.id).delete()
                # Delete order items for this order
                OrderItem.query.filter_by(order_id=order.id).delete()
                # Delete the order
                db.session.delete(order)
            print(f"   ✅ Deleted {len(orders)} orders and related records")
        
        # 9. Delete products (for sellers)
        if user.user_type == 'seller':
            products = Product.query.filter_by(seller_id=user.id).all()
            for product in products:
                # Delete order items for this product
                OrderItem.query.filter_by(product_id=product.id).delete()
                # Delete wishlist items for this product
                Wishlist.query.filter_by(product_id=product.id).delete()
                # Delete cart items for this product
                Cart.query.filter_by(product_id=product.id).delete()
                # Delete reviews for this product
                Review.query.filter_by(product_id=product.id).delete()
                # Delete product image if exists
                if product.image:
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], product.image)
                    if os.path.exists(image_path):
                        os.remove(image_path)
                db.session.delete(product)
            print(f"   ✅ Deleted {len(products)} products")
        
        # 10. Delete password reset tokens
        PasswordReset.query.filter_by(email=user.email).delete()
        print("   ✅ Deleted password reset tokens")
        
        # 11. Delete email verifications
        EmailVerification.query.filter_by(email=user.email).delete()
        print("   ✅ Deleted email verifications")
        
        # 12. Delete delivery person related records
        if hasattr(user, 'delivery_assignments'):
            DeliveryAssignment.query.filter_by(delivery_person_id=user.id).delete()
            DeliveryTracking.query.filter_by(delivery_person_id=user.id).delete()
            DeliveryProof.query.filter_by(delivery_person_id=user.id).delete()
            print("   ✅ Deleted delivery person records")
        
        # 13. Delete user's profile image
        if user.profile_image:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], user.profile_image)
            if os.path.exists(image_path):
                os.remove(image_path)
            print("   ✅ Deleted profile image")
        
        # 14. Finally, delete the user
        db.session.delete(user)
        db.session.commit()
        
        print(f"✅ User {user_name} deleted successfully!")
        print(f"{'='*50}\n")
        
        return jsonify({
            'success': True,
            'message': f'User {user_name} deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error deleting user: {e}")
        traceback.print_exc()
        
        # Try to get the specific foreign key error
        error_msg = str(e)
        if "FOREIGN KEY" in error_msg or "foreign key" in error_msg:
            # Extract table name from error if possible
            import re
            match = re.search(r'FOREIGN KEY constraint failed', error_msg)
            if match:
                error_msg = "Cannot delete user because they have related records. Please remove all orders, products, and other related data first."
        
        return jsonify({'success': False, 'message': error_msg}), 500



@app.route('/seller/analytics/export')
@login_required
def export_analytics():
    """Export seller analytics to CSV"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied. Seller account required.', 'danger')
        return redirect(url_for('products'))
    
    # Get date range from request
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    
    # Set date range (default to last 30 days)
    end_date = datetime.utcnow()
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            end_date = end_date.replace(hour=23, minute=59, second=59)
        except:
            pass
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            start_date = start_date.replace(hour=0, minute=0, second=0)
        except:
            start_date = end_date - timedelta(days=30)
    else:
        start_date = end_date - timedelta(days=30)
    
    try:
        # Get orders for this seller in date range
        orders = db.session.query(Order).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.created_at >= start_date,
            Order.created_at <= end_date,
            Order.status == 'completed'
        ).distinct().order_by(Order.created_at.desc()).all()
        
        # Get order items for this seller
        order_items = OrderItem.query.filter(
            OrderItem.seller_id == user.id,
            OrderItem.order.has(Order.created_at >= start_date),
            OrderItem.order.has(Order.created_at <= end_date),
            OrderItem.order.has(Order.status == 'completed')
        ).all()
        
        # Calculate totals
        total_revenue = sum(item.price * item.quantity for item in order_items)
        total_products_sold = sum(item.quantity for item in order_items)
        total_orders = len(set(item.order_id for item in order_items))
        
        # Create CSV
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['ShopMax Seller Analytics Report'])
        writer.writerow(['Seller:', user.business_name or user.fullname])
        writer.writerow(['Period:', f'{start_date.strftime("%d %b %Y")} - {end_date.strftime("%d %b %Y")}'])
        writer.writerow(['Generated:', datetime.utcnow().strftime("%d %b %Y %H:%M")])
        writer.writerow([])
        
        # Summary section
        writer.writerow(['SUMMARY STATISTICS'])
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Total Revenue', f'UGX {total_revenue:,.0f}'])
        writer.writerow(['Total Orders', total_orders])
        writer.writerow(['Total Products Sold', total_products_sold])
        writer.writerow(['Average Order Value', f'UGX {(total_revenue/total_orders if total_orders > 0 else 0):,.0f}'])
        writer.writerow([])
        
        # Order details section
        writer.writerow(['ORDER DETAILS'])
        writer.writerow(['Order ID', 'Date', 'Customer', 'Items', 'Total', 'Status'])
        
        for order in orders:
            # Get items count for this order
            items_count = sum(item.quantity for item in order.order_items if item.seller_id == user.id)
            order_total = sum(item.price * item.quantity for item in order.order_items if item.seller_id == user.id)
            
            writer.writerow([
                f'#{order.id}',
                order.created_at.strftime('%d %b %Y') if order.created_at else 'N/A',
                order.user.fullname if order.user else 'Guest',
                items_count,
                f'UGX {order_total:,.0f}',
                order.status.upper()
            ])
        
        writer.writerow([])
        
        # Product details section
        writer.writerow(['PRODUCT DETAILS'])
        writer.writerow(['Product ID', 'Product Name', 'Quantity Sold', 'Revenue', 'Price Per Unit'])
        
        # Group by product
        product_stats = {}
        for item in order_items:
            product_id = item.product_id
            if product_id not in product_stats:
                product_stats[product_id] = {
                    'name': item.product.name if item.product else 'Unknown',
                    'quantity': 0,
                    'revenue': 0,
                    'price': item.price
                }
            product_stats[product_id]['quantity'] += item.quantity
            product_stats[product_id]['revenue'] += item.price * item.quantity
        
        for product_id, stats in product_stats.items():
            writer.writerow([
                product_id,
                stats['name'],
                stats['quantity'],
                f'UGX {stats["revenue"]:,.0f}',
                f'UGX {stats["price"]:,.0f}'
            ])
        
        # Get the CSV content
        output.seek(0)
        csv_content = output.getvalue()
        output.close()
        
        # Create response
        from flask import make_response
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=shopmax_analytics_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error exporting analytics: {e}")
        traceback.print_exc()
        flash(f'Error exporting data: {str(e)}', 'danger')
        return redirect(url_for('seller_analytics'))





@app.route('/fix-subscription-logic')
@admin_required
def fix_subscription_logic():
    """Complete fix for all sellers subscription status"""
    sellers = User.query.filter_by(user_type='seller').all()
    
    fixed_count = 0
    for seller in sellers:
        # Check if subscription is active (has expiry and not expired)
        is_active = seller.subscription_expiry and seller.subscription_expiry > datetime.utcnow()
        
        if not is_active:
            # No active subscription = should be None
            if seller.subscription_tier is not None:
                print(f"Fixing seller {seller.id}: {seller.subscription_tier} -> None")
                seller.subscription_tier = None
                seller.subscription_expiry = None
                fixed_count += 1
    
    db.session.commit()
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Subscription Fix Complete</title>
        <style>
            body {{ font-family: Arial; padding: 40px; background: #f5f5f5; }}
            .card {{ background: white; padding: 30px; border-radius: 10px; max-width: 600px; margin: 0 auto; }}
            .success {{ color: green; font-size: 24px; }}
            .btn {{ background: #ff6b00; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1 class="success">✅ Subscription Logic Fixed!</h1>
            <p>Fixed <strong>{fixed_count}</strong> sellers with incorrect subscription status.</p>
            <p>All sellers with no active subscription now have:</p>
            <ul>
                <li><strong>subscription_tier = None</strong></li>
                <li><strong>subscription_expiry = None</strong></li>
            </ul>
            <a href="/debug-subscription-status" class="btn">Check Your Status</a>
            <a href="/seller/subscription" class="btn" style="background: #000080;">Go to Subscription</a>
        </div>
    </body>
    </html>
    """







@app.route('/generate_report')
@admin_required
def generate_report():
    """Generate and download reports"""
    report_type = request.args.get('type', 'sales')
    format_type = request.args.get('format', 'csv')
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    
    # Build filters based on date range
    filters = []
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            filters.append(Order.created_at >= start)
        except ValueError:
            pass
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            filters.append(Order.created_at < end)
        except ValueError:
            pass
    
    # Generate appropriate report
    if report_type == 'sales':
        return generate_sales_report(filters, format_type)
    elif report_type == 'users':
        return generate_users_report(filters, format_type)
    elif report_type == 'products':
        return generate_products_report(filters, format_type)
    elif report_type == 'sellers':
        return generate_sellers_report(filters, format_type)
    else:
        flash('Invalid report type', 'danger')
        return redirect(url_for('admin_reports'))



@app.route('/report-issue', methods=['GET', 'POST'])
@login_required
def report_issue():
    """Report an issue with an order"""
    user = get_current_user()
    
    if request.method == 'POST':
        try:
            issue_type = request.form.get('issue_type')
            order_id = request.form.get('order_id')
            product_name = request.form.get('product_name')
            seller_name = request.form.get('seller_name')
            description = request.form.get('description')
            contact_method = request.form.get('contact_method')
            
            # Validate required fields
            if not issue_type or not description:
                flash('Please provide both issue type and description', 'danger')
                return render_template('report_issue.html')
            
            # Find order if order_id provided
            order = None
            if order_id and order_id.strip():
                try:
                    # Remove # if present
                    order_id_clean = order_id.lstrip('#')
                    order = Order.query.get(int(order_id_clean))
                    if order and order.user_id != user.id:
                        flash('You can only report issues for your own orders', 'danger')
                        return render_template('report_issue.html')
                except (ValueError, TypeError):
                    pass
            
            # Create issue record
            issue = Issue(
                order_id=order.id if order else None,
                user_id=user.id,
                issue_type=issue_type,
                description=description,
                status='pending',
                priority='medium',
                created_at=datetime.utcnow()
            )
            db.session.add(issue)
            
            # Add initial customer message
            initial_message = IssueMessage(
                issue_id=issue.id,
                sender_id=user.id,
                message=description,
                is_admin_reply=False,
                created_at=datetime.utcnow()
            )
            db.session.add(initial_message)
            
            # If product name or seller name provided, add to description
            if product_name or seller_name:
                additional_info = []
                if product_name:
                    additional_info.append(f"Product: {product_name}")
                if seller_name:
                    additional_info.append(f"Seller: {seller_name}")
                issue.description += f"\n\nAdditional Info: {', '.join(additional_info)}"
            
            db.session.commit()
            
            # Send notification to admin (optional)
            admins = User.query.filter_by(user_type='admin').all()
            for admin in admins:
                # Create admin notification (you can implement this)
                pass
            
            flash('Your issue has been reported successfully. Our team will contact you within 24-48 hours.', 'success')
            return redirect(url_for('orders'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error reporting issue: {e}")
            traceback.print_exc()
            flash('Error reporting issue. Please try again.', 'danger')
            return render_template('report_issue.html')
    
    # GET request - show form
    return render_template('report_issue.html')



def is_new_seller(user):
    """Check if seller has never subscribed before"""
    # A seller is considered new if:
    # 1. They have no subscription tier (None or empty)
    # 2. They have no subscription expiry date
    # 3. They have never had any subscription before
    return (user.subscription_tier is None or user.subscription_tier == '') and user.subscription_expiry is None




@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    """Update user profile - handles both form and AJAX requests"""
    user = get_current_user()
    
    try:
        # Check if it's an AJAX request (for avatar upload)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        # Handle profile image upload
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename and allowed_file(file.filename):
                # Delete old image if exists
                if user.profile_image:
                    old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], user.profile_image)
                    if os.path.exists(old_image_path):
                        try:
                            os.remove(old_image_path)
                        except:
                            pass
                
                # Save new image
                filename = secure_filename(file.filename)
                timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
                new_filename = timestamp + filename
                ensure_upload_folder()
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], new_filename))
                user.profile_image = new_filename
                
                db.session.commit()
                
                if is_ajax:
                    return jsonify({
                        'success': True,
                        'message': 'Profile picture updated',
                        'user': {
                            'id': user.id,
                            'fullname': user.fullname,
                            'profile_image': user.profile_image
                        }
                    })
        
        # Handle regular form submission
        if request.form:
            # Update basic info
            if 'fullname' in request.form:
                user.fullname = request.form.get('fullname', user.fullname)
            if 'phone' in request.form:
                user.phone = request.form.get('phone', user.phone)
            if 'location' in request.form:
                user.location = request.form.get('location', user.location)
            
            # Buyer-specific fields
            if user.user_type == 'buyer' and 'delivery_address' in request.form:
                user.delivery_address = request.form.get('delivery_address', user.delivery_address)
            
            # Seller-specific fields
            if user.user_type == 'seller':
                if 'business_name' in request.form:
                    user.business_name = request.form.get('business_name', user.business_name)
                if 'business_address' in request.form:
                    user.business_address = request.form.get('business_address', user.business_address)
            
            user.updated_at = datetime.utcnow()
            db.session.commit()
            
            # Update session name if changed
            if 'fullname' in request.form:
                session['user_name'] = user.fullname
            
            if is_ajax:
                return jsonify({
                    'success': True,
                    'message': 'Profile updated successfully',
                    'user': {
                        'id': user.id,
                        'fullname': user.fullname,
                        'email': user.email,
                        'phone': user.phone,
                        'location': user.location,
                        'profile_image': user.profile_image
                    }
                })
        
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating profile: {e}")
        traceback.print_exc()
        
        if is_ajax:
            return jsonify({'success': False, 'message': str(e)}), 500
        
        flash(f'Error updating profile: {str(e)}', 'danger')
        return redirect(url_for('profile'))


@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    """Change user password"""
    user = get_current_user()
    
    try:
        data = request.get_json()
        
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')
        
        # Validate
        if not current_password or not new_password or not confirm_password:
            return jsonify({'success': False, 'message': 'All fields are required'}), 400
        
        if new_password != confirm_password:
            return jsonify({'success': False, 'message': 'New passwords do not match'}), 400
        
        if len(new_password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
        
        # Check current password
        if not check_password_hash(user.password, current_password):
            return jsonify({'success': False, 'message': 'Current password is incorrect'}), 400
        
        # Update password
        user.password = generate_password_hash(new_password)
        user.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Password changed successfully'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error changing password: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500






@app.route('/seller/subscription-fees')
@login_required
def subscription_fees():
    """Subscription fees page"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('This page is for sellers only.', 'danger')
        return redirect(url_for('products'))
    
    return render_template('subscription_fees.html', user=user, now=datetime.utcnow)




@app.route('/api/admin/issues/<int:issue_id>', methods=['GET'])
@admin_required
def get_issue_details_with_messages(issue_id):
    """Get detailed issue information with full message thread"""
    try:
        # Get issue with all related data
        issue = Issue.query.options(
            db.joinedload(Issue.user),
            db.joinedload(Issue.order).joinedload(Order.order_items).joinedload(OrderItem.product),
            db.joinedload(Issue.messages).joinedload(IssueMessage.sender)
        ).get_or_404(issue_id)
        
        # Get all messages for this issue
        messages = IssueMessage.query.filter_by(issue_id=issue_id)\
            .order_by(IssueMessage.created_at.asc())\
            .all()
        
        messages_data = []
        for msg in messages:
            messages_data.append({
                'id': msg.id,
                'message': msg.message,
                'sender_name': msg.sender.fullname if msg.sender else 'Unknown',
                'is_admin_reply': msg.is_admin_reply,
                'created_at': msg.created_at.strftime('%d %b %Y %H:%M'),
                'sender_id': msg.sender_id
            })
        
        # Get order items
        order_items = []
        if issue.order:
            for item in issue.order.order_items:
                order_items.append({
                    'id': item.id,
                    'product_name': item.product.name if item.product else 'Product',
                    'quantity': item.quantity,
                    'price': float(item.price),
                    'total': float(item.price * item.quantity),
                    'image': url_for('static', filename='uploads/' + item.product.image) if item.product and item.product.image else None
                })
        
        return jsonify({
            'success': True,
            'issue': {
                'id': issue.id,
                'order_id': issue.order_id,
                'issue_type': issue.issue_type,
                'description': issue.description,
                'status': issue.status,
                'priority': issue.priority,
                'action_taken': issue.action_taken or '',
                'resolution_notes': issue.resolution_notes or '',
                'admin_response': issue.admin_response or '',
                'responder_name': issue.responder.fullname if issue.responder else None,
                'responded_at': issue.responded_at.strftime('%d %b %Y %H:%M') if issue.responded_at else None,
                'created_at': issue.created_at.strftime('%d %b %Y %H:%M'),
                'order_total': float(issue.order.total_amount) if issue.order else 0
            },
            'messages': messages_data,
            'order_items': order_items,
            'customer': {
                'id': issue.user.id if issue.user else None,
                'name': issue.user.fullname if issue.user else 'Unknown',
                'email': issue.user.email if issue.user else 'N/A',
                'phone': issue.user.phone if issue.user else 'N/A',
                'joined': issue.user.created_at.strftime('%d %b %Y') if issue.user and issue.user.created_at else 'N/A'
            },
            'order': {
                'id': issue.order.id if issue.order else None,
                'total': float(issue.order.total_amount) if issue.order else 0,
                'status': issue.order.status if issue.order else None,
                'created_at': issue.order.created_at.strftime('%d %b %Y %H:%M') if issue.order else None,
                'delivery_address': issue.order.delivery_address if issue.order else None
            }
        })
        
    except Exception as e:
        print(f"Error getting issue details: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/api/admin/issues/<int:issue_id>/update', methods=['POST'])
@admin_required
def update_issue(issue_id):
    """Update issue priority and status"""
    try:
        data = request.get_json()
        print(f"📝 Updating issue {issue_id}: {data}")  # Debug log
        
        issue = Issue.query.get_or_404(issue_id)
        
        if 'priority' in data and data['priority']:
            issue.priority = data['priority']
            print(f"   Updated priority to: {issue.priority}")
            
        if 'status' in data and data['status']:
            issue.status = data['status']
            print(f"   Updated status to: {issue.status}")
            if data['status'] == 'resolved':
                issue.resolved_at = datetime.utcnow()
                print(f"   Set resolved_at to: {issue.resolved_at}")
        
        issue.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Issue updated successfully',
            'issue': {
                'id': issue.id,
                'status': issue.status,
                'priority': issue.priority
            }
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error updating issue: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/admin/products')
@admin_required
def admin_products():
    """Admin products management page"""
    user = get_current_user()
    
    # Get filter parameters
    search = request.args.get('search', '')
    category = request.args.get('category', '')
    status = request.args.get('status', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Build query
    query = Product.query.options(db.joinedload(Product.seller))
    
    if search:
        query = query.filter(
            db.or_(
                Product.name.ilike(f'%{search}%'),
                Product.description.ilike(f'%{search}%')
            )
        )
    
    if category:
        query = query.filter_by(category=category)
    
    if status == 'active':
        query = query.filter_by(is_active=True)
    elif status == 'inactive':
        query = query.filter_by(is_active=False)
    
    # Get paginated results
    pagination = query.order_by(Product.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    products = pagination.items
    
    # Get statistics for stats cards
    total_products = Product.query.count()
    active_products = Product.query.filter_by(is_active=True).count()
    inactive_products = total_products - active_products
    
    # Calculate inventory value - SUM of (price * stock) for active products
    inventory_value = db.session.query(
        db.func.sum(Product.price * Product.stock)
    ).filter(Product.is_active == True).scalar() or 0
    
    # Get category counts (for display)
    category_counts = db.session.query(
        Product.category, 
        db.func.count(Product.id)
    ).group_by(Product.category).all()
    
    # Debug prints
    print("="*50)
    print("🔍 ADMIN PRODUCTS PAGE")
    print(f"total_products: {total_products}")
    print(f"active_products: {active_products}")
    print(f"inactive_products: {inactive_products}")
    print(f"inventory_value: UGX {inventory_value:,.0f}")
    print("="*50)
    
    return render_template('admin_products.html',
                         user=user,
                         products=products,
                         pagination=pagination,
                         total_products=total_products,
                         active_products=active_products,
                         inactive_products=inactive_products,
                         inventory_value=inventory_value,
                         category_counts=category_counts,
                         search=search,
                         category=category,
                         status=status)



@app.route('/api/admin/issues/<int:issue_id>', methods=['GET'])
@admin_required
def get_issue_details(issue_id):
    """Get detailed issue information with full message thread"""
    try:
        print(f"🔍 Fetching issue details for ID: {issue_id}")  # Debug log
        
        # Get issue with all related data
        issue = Issue.query.options(
            db.joinedload(Issue.user),
            db.joinedload(Issue.order).joinedload(Order.order_items).joinedload(OrderItem.product),
            db.joinedload(Issue.messages).joinedload(IssueMessage.sender)
        ).get_or_404(issue_id)
        
        print(f"   Found issue: #{issue.id} - {issue.issue_type}")
        
        # Get all messages for this issue
        messages = IssueMessage.query.filter_by(issue_id=issue_id)\
            .order_by(IssueMessage.created_at.asc())\
            .all()
        
        print(f"   Found {len(messages)} messages")
        
        messages_data = []
        for msg in messages:
            messages_data.append({
                'id': msg.id,
                'message': msg.message,
                'sender_name': msg.sender.fullname if msg.sender else 'Unknown',
                'is_admin_reply': msg.is_admin_reply,
                'created_at': msg.created_at.strftime('%d %b %Y %H:%M'),
                'sender_id': msg.sender_id
            })
        
        # Get order items
        order_items = []
        if issue.order:
            for item in issue.order.order_items:
                order_items.append({
                    'id': item.id,
                    'product_name': item.product.name if item.product else 'Product',
                    'quantity': item.quantity,
                    'price': float(item.price),
                    'total': float(item.price * item.quantity),
                    'image': url_for('static', filename='uploads/' + item.product.image) if item.product and item.product.image else None
                })
        
        response_data = {
            'success': True,
            'issue': {
                'id': issue.id,
                'order_id': issue.order_id,
                'issue_type': issue.issue_type,
                'description': issue.description,
                'status': issue.status,
                'priority': issue.priority,
                'action_taken': issue.action_taken or '',
                'resolution_notes': issue.resolution_notes or '',
                'admin_response': issue.admin_response or '',
                'responder_name': issue.responder.fullname if issue.responder else None,
                'responded_at': issue.responded_at.strftime('%d %b %Y %H:%M') if issue.responded_at else None,
                'created_at': issue.created_at.strftime('%d %b %Y %H:%M'),
                'order_total': float(issue.order.total_amount) if issue.order else 0
            },
            'messages': messages_data,
            'order_items': order_items,
            'customer': {
                'id': issue.user.id if issue.user else None,
                'name': issue.user.fullname if issue.user else 'Unknown',
                'email': issue.user.email if issue.user else 'N/A',
                'phone': issue.user.phone if issue.user else 'N/A',
                'joined': issue.user.created_at.strftime('%d %b %Y') if issue.user and issue.user.created_at else 'N/A'
            },
            'order': {
                'id': issue.order.id if issue.order else None,
                'total': float(issue.order.total_amount) if issue.order else 0,
                'status': issue.order.status if issue.order else None,
                'created_at': issue.order.created_at.strftime('%d %b %Y %H:%M') if issue.order else None,
                'delivery_address': issue.order.delivery_address if issue.order else None
            }
        }
        
        print(f"✅ Returning response for issue {issue_id}")
        return jsonify(response_data)
        
    except Exception as e:
        print(f"❌ Error getting issue details: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500








# ==================== SOCKET.IO INITIALIZATION ====================
# ADD THIS RIGHT HERE - AFTER ALL HELPER FUNCTIONS BUT BEFORE ANY ROUTES
from flask_cors import CORS

CORS(app)




@app.route('/api/admin/products/<int:product_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_product(product_id):
    """Toggle product active status"""
    try:
        product = Product.query.get_or_404(product_id)
        product.is_active = not product.is_active
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Product {"activated" if product.is_active else "deactivated"}',
            'is_active': product.is_active
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error toggling product: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500




@app.route('/api/admin/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def admin_delete_product(product_id):
    """Delete product permanently"""
    try:
        product = Product.query.get_or_404(product_id)
        
        # Delete product image if exists
        if product.image:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], product.image)
            if os.path.exists(image_path):
                os.remove(image_path)
        
        # Delete order items associated with this product first
        OrderItem.query.filter_by(product_id=product_id).delete()
        
        # Delete wishlist items
        Wishlist.query.filter_by(product_id=product_id).delete()
        
        # Delete cart items
        Cart.query.filter_by(product_id=product_id).delete()
        
        # Delete reviews
        Review.query.filter_by(product_id=product_id).delete()
        
        # Delete the product
        db.session.delete(product)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Product deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting product: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500   











@app.route('/api/admin/products/export', methods=['GET'])
@admin_required
def export_products():
    """Export products to CSV"""
    try:
        # Get filter parameters
        search = request.args.get('search', '')
        status = request.args.get('status', '')
        
        # Build query
        query = Product.query.options(db.joinedload(Product.seller))
        
        if search:
            query = query.filter(
                db.or_(
                    Product.name.ilike(f'%{search}%'),
                    Product.description.ilike(f'%{search}%')
                )
            )
        
        if status == 'active':
            query = query.filter_by(is_active=True)
        elif status == 'inactive':
            query = query.filter_by(is_active=False)
        
        products = query.order_by(Product.created_at.desc()).all()
        
        # Create CSV
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow(['ID', 'Name', 'Seller', 'Category', 'Price', 'Stock', 'Sold', 'Status', 'Created'])
        
        # Write data
        for product in products:
            writer.writerow([
                product.id,
                product.name,
                product.seller.business_name or product.seller.fullname if product.seller else 'N/A',
                product.category,
                product.price,
                product.stock,
                product.sold_count or 0,
                'Active' if product.is_active else 'Inactive',
                product.created_at.strftime('%Y-%m-%d') if product.created_at else 'N/A'
            ])
        
        # Create response
        csv_content = output.getvalue()
        output.close()
        
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=products_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error exporting products: {e}")
        traceback.print_exc()
        flash('Error exporting products', 'danger')
        return redirect(url_for('admin_products'))           




# ==================== TRACKING ROUTES ====================
# Rider mobile interface
@app.route('/rider/tracking/<int:rider_id>')
@login_required
def rider_tracking_portal(rider_id):
    """Mobile-optimized page for riders to share location"""
    rider = DeliveryPerson.query.get_or_404(rider_id)
    
    # Get active assignments
    active_assignments = DeliveryAssignment.query.filter_by(
        delivery_person_id=rider_id,
        status='assigned'
    ).all()
    
    orders = []
    for assignment in active_assignments:
        order = assignment.order
        orders.append({
            'id': order.id,
            'customer_name': order.user.fullname if order.user else 'Unknown',
            'customer_phone': order.user.phone if order.user else 'N/A',
            'delivery_address': order.delivery_address,
            'status': order.status
        })
    
    return render_template('rider_tracking_portal.html',
                         rider=rider,
                         orders=orders)

# Customer tracking page
@app.route('/track-order/<int:order_id>')
@login_required
def track_order(order_id):
    """Customer order tracking page with live map"""
    order = Order.query.get_or_404(order_id)
    user = get_current_user()
    
    # Security check
    if user.user_type == 'buyer' and order.user_id != user.id:
        flash('You can only track your own orders', 'danger')
        return redirect(url_for('orders'))
    
    # Get tracking history
    tracking_points = DeliveryTracking.query.filter_by(order_id=order_id)\
        .order_by(DeliveryTracking.created_at).all()
    
    # Get rider info
    rider = order.delivery_person
    
    # Get last known location
    last_location = None
    if tracking_points:
        last_point = tracking_points[-1]
        last_location = {
            'lat': last_point.latitude,
            'lng': last_point.longitude
        }
    
    # Calculate progress
    steps = ['pending', 'confirmed', 'processing', 'out_for_delivery', 'delivered']
    status_map = {
        'pending': 0,
        'confirmed': 1,
        'processing': 2,
        'out_for_delivery': 3,
        'shipped': 3,
        'in_transit': 3,
        'delivered': 4,
        'completed': 4
    }
    current_step = status_map.get(order.status, 0)
    
    return render_template('customer_tracking.html',
                         order=order,
                         rider=rider,
                         tracking_points=tracking_points,
                         last_location=last_location,
                         current_step=current_step,
                         steps=steps,
                         now=datetime.utcnow())


@app.route('/debug/check-admin')
def debug_check_admin():
    """Check if admin exists"""
    admin = User.query.filter_by(user_type='admin').first()
    if admin:
        return f"✅ Admin found: {admin.fullname} (ID: {admin.id}, Email: {admin.email})"
    else:
        return "❌ No admin found! Please create one."






@app.route('/api/admin/messages/conversations')
@admin_required
def api_admin_messages_conversations():
    """Get all conversations for admin (from sellers AND buyers)"""
    try:
        admin_id = session['user_id']
        
        # Get all conversations where admin is participant
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == admin_id, Conversation.participant1_type == 'admin'),
                db.and_(Conversation.participant2_id == admin_id, Conversation.participant2_type == 'admin')
            )
        ).order_by(Conversation.updated_at.desc()).all()
        
        result = []
        for conv in conversations:
            # Determine other participant
            if conv.participant1_id == admin_id and conv.participant1_type == 'admin':
                other_id = conv.participant2_id
                other_type = conv.participant2_type
            else:
                other_id = conv.participant1_id
                other_type = conv.participant1_type
            
            # Get user details (works for both sellers AND buyers)
            user = User.query.get(other_id)
            if not user:
                continue
            
            # Get last message
            last_message = Message.query.filter_by(conversation_id=conv.id).order_by(Message.created_at.desc()).first()
            
            # Get unread count
            unread_count = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != admin_id,
                Message.is_read == False
            ).count()
            
            # Determine user type display
            user_type_display = user.user_type.upper() if user.user_type else 'USER'
            user_icon = 'fas fa-store' if user.user_type == 'seller' else 'fas fa-user'
            
            result.append({
                'id': conv.id,
                'type': conv.conversation_type,
                'other_participant': {
                    'id': user.id,
                    'name': user.fullname,
                    'type': user.user_type,
                    'type_display': user_type_display,
                    'icon': user_icon,
                    'email': user.email,
                    'phone': user.phone,
                    'avatar': user.profile_image,
                    'business_name': user.business_name if user.user_type == 'seller' else None
                },
                'last_message': {
                    'id': last_message.id,
                    'content': last_message.content[:50] + '...' if last_message and len(last_message.content) > 50 else (last_message.content if last_message else ''),
                    'created_at': last_message.created_at.isoformat() if last_message else None,
                    'sender_id': last_message.sender_id if last_message else None
                } if last_message else None,
                'unread_count': unread_count,
                'product': {
                    'id': conv.product_id,
                } if conv.product_id else None,
                'created_at': conv.created_at.isoformat() if conv.created_at else None,
                'updated_at': conv.updated_at.isoformat() if conv.updated_at else None
            })
        
        return jsonify({'success': True, 'data': result})
        
    except Exception as e:
        print(f"Error in admin messages: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/place_order', methods=['POST'])
@login_required
def place_order():
    user = get_current_user()
    
    if user.user_type != 'buyer':
        flash('Only buyers can place orders.', 'danger')
        return redirect(url_for('products'))
    
    # Get cart items
    cart_items = db.session.query(Cart, Product).\
        join(Product, Cart.product_id == Product.id).\
        filter(Cart.user_id == user.id).all()
    
    if not cart_items:
        flash('Your cart is empty.', 'danger')
        return redirect(url_for('view_cart'))
    
    # Get form data
    delivery_address = request.form.get('delivery_address', '').strip()
    phone = request.form.get('phone', '').strip()
    payment_method = request.form.get('payment_method', 'cash_on_delivery')
    
    # Calculate subtotal - NO DELIVERY FEE
    subtotal = 0
    for cart_item, product in cart_items:
        if product.stock < cart_item.quantity:
            flash(f'Sorry, "{product.name}" only has {product.stock} items in stock.', 'danger')
            return redirect(url_for('view_cart'))
        subtotal += product.price * cart_item.quantity
    
    # Total is just subtotal (no delivery fee)
    total = subtotal
    
    try:
        # Create order
        new_order = Order(
            total_amount=total,
            delivery_address=delivery_address,
            payment_method=payment_method,
            payment_status='pending',
            user_id=user.id,
            status='confirmed'
        )
        db.session.add(new_order)
        db.session.flush()
        
        # Add tracking
        tracking = OrderTracking(
            order_id=new_order.id,
            status='confirmed',
            notes='Order placed successfully'
        )
        db.session.add(tracking)
        
        # Add order items
        for cart_item, product in cart_items:
            order_item = OrderItem(
                order_id=new_order.id,
                product_id=product.id,
                seller_id=product.seller_id,
                quantity=cart_item.quantity,
                price=product.price
            )
            db.session.add(order_item)
            
            # Update product stock
            product.stock -= cart_item.quantity
            product.sold_count += cart_item.quantity
        
        # Clear cart
        Cart.query.filter_by(user_id=user.id).delete()
        
        db.session.commit()
        
        # Get order items for confirmation
        order_items = OrderItem.query.filter_by(order_id=new_order.id).all()
        
        # Send notifications
        send_order_notifications(new_order, order_items)
        
        return render_template('order_confirmation.html',
                             order=new_order,
                             order_items=order_items,
                             user=user)
    
    except Exception as e:
        db.session.rollback()
        print(f"Error placing order: {str(e)}")
        traceback.print_exc()
        flash('Error placing order. Please try again.', 'danger')
        return redirect(url_for('checkout'))





# ============ API ENDPOINTS FOR CHAT ============

@app.route('/api/messages/conversations')
@login_required
def api_get_conversations():
    """Get all conversations for current user"""
    try:
        user_id = session['user_id']
        user_type = session['user_type']
        
        # Get conversations where user is participant
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == user_id, Conversation.participant1_type == user_type),
                db.and_(Conversation.participant2_id == user_id, Conversation.participant2_type == user_type)
            )
        ).order_by(Conversation.updated_at.desc()).all()
        
        result = []
        for conv in conversations:
            # Determine other participant
            if conv.participant1_id == user_id and conv.participant1_type == user_type:
                other_id = conv.participant2_id
                other_type = conv.participant2_type
            else:
                other_id = conv.participant1_id
                other_type = conv.participant1_type
            
            # Get other user details
            other_user = None
            if other_type == 'buyer':
                other_user = User.query.get(other_id)
            elif other_type == 'seller':
                other_user = User.query.get(other_id)
            elif other_type == 'admin':
                other_user = User.query.get(other_id)
            
            if not other_user:
                continue
            
            # Get last message
            last_message = Message.query.filter_by(conversation_id=conv.id).order_by(Message.created_at.desc()).first()
            
            # Get unread count
            unread_count = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != user_id,
                Message.is_read == False
            ).count()
            
            # Get product details if any
            product = None
            if conv.product_id:
                product = Product.query.get(conv.product_id)
            
            # Get order details if any
            order = None
            if conv.order_id:
                order = Order.query.get(conv.order_id)
            
            result.append({
                'id': conv.id,
                'type': conv.conversation_type,
                'other_participant': {
                    'id': other_id,
                    'name': other_user.fullname or other_user.business_name or 'User',
                    'type': other_type,
                    'avatar': other_user.profile_image if other_user else None,
                    'email': other_user.email if other_user else None,
                    'phone': other_user.phone if other_user else None
                },
                'last_message': {
                    'id': last_message.id,
                    'content': last_message.content[:50] + '...' if last_message and len(last_message.content) > 50 else (last_message.content if last_message else ''),
                    'created_at': last_message.created_at.isoformat() if last_message else None,
                    'sender_id': last_message.sender_id if last_message else None
                } if last_message else None,
                'unread_count': unread_count,
                'product': {
                    'id': product.id,
                    'name': product.name,
                    'price': float(product.price),
                    'image': product.image
                } if product else None,
                'order': {
                    'id': order.id,
                    'total': float(order.total_amount) if order.total_amount else 0,
                    'status': order.status
                } if order else None,
                'updated_at': conv.updated_at.isoformat() if conv.updated_at else None,
                'created_at': conv.created_at.isoformat() if conv.created_at else None
            })
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"Error in api_get_conversations: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/messages/conversations/<int:conversation_id>/messages')
@login_required
def api_get_conversation_messages(conversation_id):
    """Get messages for a specific conversation"""
    try:
        user_id = session['user_id']
        
        # Verify user is part of conversation
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return jsonify({'success': False, 'error': 'Conversation not found'}), 404
        
        if not (conversation.participant1_id == user_id or conversation.participant2_id == user_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        # Get messages
        messages = Message.query.filter_by(
            conversation_id=conversation_id,
            is_deleted=False
        ).order_by(Message.created_at.asc()).all()
        
        result = []
        for msg in messages:
            # Get sender details
            sender = None
            if msg.sender_type == 'buyer':
                sender = User.query.get(msg.sender_id)
            elif msg.sender_type == 'seller':
                sender = User.query.get(msg.sender_id)
            elif msg.sender_type == 'admin':
                sender = User.query.get(msg.sender_id)
            
            result.append({
                'id': msg.id,
                'conversation_id': msg.conversation_id,
                'sender_id': msg.sender_id,
                'sender_type': msg.sender_type,
                'sender_name': sender.fullname or sender.business_name if sender else 'Unknown',
                'content': msg.content,
                'message_type': msg.message_type,
                'attachments': json.loads(msg.attachments) if msg.attachments else [],
                'is_read': msg.is_read,
                'is_delivered': msg.is_delivered,
                'is_edited': msg.is_edited,
                'created_at': msg.created_at.isoformat() if msg.created_at else None
            })
        
        # Mark messages as read
        unread_messages = Message.query.filter(
            Message.conversation_id == conversation_id,
            Message.sender_id != user_id,
            Message.is_read == False
        ).all()
        
        for msg in unread_messages:
            msg.is_read = True
        
        if unread_messages:
            db.session.commit()
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"Error in api_get_conversation_messages: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/messages/send', methods=['POST'])
@login_required
def api_send_message():
    """Send a new message"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        user_type = session['user_type']
        
        conversation_id = data.get('conversation_id')
        content = data.get('content', '').strip()
        attachments = data.get('attachments', [])
        message_type = data.get('message_type', 'text')
        
        if not conversation_id:
            return jsonify({'success': False, 'error': 'Conversation ID required'}), 400
        
        if not content and not attachments:
            return jsonify({'success': False, 'error': 'Message content cannot be empty'}), 400
        
        # Verify user is part of conversation
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return jsonify({'success': False, 'error': 'Conversation not found'}), 404
        
        if not (conversation.participant1_id == user_id or conversation.participant2_id == user_id):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        # Create message
        message = Message(
            conversation_id=conversation_id,
            sender_id=user_id,
            sender_type=user_type,
            content=content,
            message_type=message_type,
            attachments=json.dumps(attachments) if attachments else None,
            created_at=datetime.utcnow()
        )
        db.session.add(message)
        
        # Update conversation
        conversation.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Get sender details for response
        sender = User.query.get(user_id)
        
        return jsonify({
            'success': True,
            'data': {
                'id': message.id,
                'conversation_id': message.conversation_id,
                'sender_id': message.sender_id,
                'sender_type': message.sender_type,
                'sender_name': sender.fullname or sender.business_name if sender else 'Unknown',
                'content': message.content,
                'message_type': message.message_type,
                'attachments': json.loads(message.attachments) if message.attachments else [],
                'is_read': message.is_read,
                'is_delivered': message.is_delivered,
                'created_at': message.created_at.isoformat() if message.created_at else None
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error in api_send_message: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/messages/start-conversation', methods=['POST'])
@login_required
def api_start_conversation():
    """Start a new conversation with seller or admin"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        user_type = session['user_type']
        
        seller_id = data.get('seller_id')
        admin_id = data.get('admin_id')
        product_id = data.get('product_id')
        order_id = data.get('order_id')
        initial_message = data.get('initial_message', 'Hello')
        
        # Determine recipient
        if seller_id:
            recipient_id = seller_id
            recipient_type = 'seller'
            conv_type = 'buyer-seller'
        elif admin_id:
            recipient_id = admin_id
            recipient_type = 'admin'
            conv_type = 'buyer-admin'
        else:
            return jsonify({'success': False, 'error': 'No recipient specified'}), 400
        
        # Check if conversation already exists
        existing = Conversation.query.filter(
            db.or_(
                db.and_(
                    Conversation.participant1_id == user_id,
                    Conversation.participant1_type == user_type,
                    Conversation.participant2_id == recipient_id,
                    Conversation.participant2_type == recipient_type
                ),
                db.and_(
                    Conversation.participant1_id == recipient_id,
                    Conversation.participant1_type == recipient_type,
                    Conversation.participant2_id == user_id,
                    Conversation.participant2_type == user_type
                )
            ),
            Conversation.conversation_type == conv_type
        ).first()
        
        if existing:
            conversation_id = existing.id
        else:
            # Create new conversation
            conversation = Conversation(
                participant1_id=user_id,
                participant1_type=user_type,
                participant2_id=recipient_id,
                participant2_type=recipient_type,
                conversation_type=conv_type,
                product_id=product_id,
                order_id=order_id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(conversation)
            db.session.flush()
            conversation_id = conversation.id
        
        # Send initial message if provided
        if initial_message and conversation_id:
            message = Message(
                conversation_id=conversation_id,
                sender_id=user_id,
                sender_type=user_type,
                content=initial_message,
                message_type='text',
                created_at=datetime.utcnow()
            )
            db.session.add(message)
            
            # Update conversation
            conv = Conversation.query.get(conversation_id)
            if conv:
                conv.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'conversation_id': conversation_id
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error in api_start_conversation: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/messages/unread-count')
@login_required
def api_unread_count():
    """Get total unread message count for user"""
    try:
        user_id = session['user_id']
        user_type = session['user_type']
        
        # Get all conversations for user
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == user_id, Conversation.participant1_type == user_type),
                db.and_(Conversation.participant2_id == user_id, Conversation.participant2_type == user_type)
            )
        ).all()
        
        total_unread = 0
        for conv in conversations:
            unread = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != user_id,
                Message.is_read == False
            ).count()
            total_unread += unread
        
        return jsonify({'success': True, 'count': total_unread})
    except Exception as e:
        print(f"Error in api_unread_count: {e}")
        return jsonify({'success': False, 'count': 0, 'error': str(e)}), 500

@app.route('/api/messages/mark-read', methods=['POST'])
@login_required
def api_mark_messages_read():
    """Mark messages as read"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        message_ids = data.get('message_ids', [])
        
        if not message_ids:
            return jsonify({'success': False, 'error': 'No message IDs provided'}), 400
        
        messages = Message.query.filter(Message.id.in_(message_ids)).all()
        
        for msg in messages:
            if msg.sender_id != user_id:  # Don't mark own messages as read
                msg.is_read = True
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(f"Error in api_mark_messages_read: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/order/<int:order_id>', methods=['GET', 'POST'])
@login_required
def order_details(order_id):
    user = get_current_user()
    order = Order.query.get_or_404(order_id)
    
    # Handle delivery confirmation via POST
    if request.method == 'POST' and request.form.get('action') == 'confirm_delivery':
        if order.user_id == user.id and order.status == 'shipped':
            order.status = 'completed'
            order.actual_delivery = datetime.utcnow()
            
            tracking = OrderTracking(
                order_id=order_id,
                status='delivered',
                notes='Buyer confirmed delivery'
            )
            db.session.add(tracking)
            db.session.commit()
            flash('✅ Delivery confirmed! Thank you for shopping with ShopMax.', 'success')
            return redirect(url_for('order_details', order_id=order_id))
    
    # Check permissions
    if user.user_type == 'buyer' and order.user_id != user.id:
        flash('You can only view your own orders.', 'danger')
        return redirect(url_for('orders'))
    
    if user.user_type == 'seller':
        seller_order_items = OrderItem.query.filter_by(
            order_id=order_id, 
            seller_id=user.id
        ).all()
        if not seller_order_items:
            flash('You can only view orders for your products.', 'danger')
            return redirect(url_for('orders'))
    
    order_items = OrderItem.query.filter_by(order_id=order_id).all()
    tracking_updates = OrderTracking.query.filter_by(order_id=order_id).order_by(OrderTracking.created_at).all()
    delivery_assignment = DeliveryAssignment.query.filter_by(order_id=order_id).first()
    
    # GET THE RIDER
    rider = None
    if order.delivery_person_id:
        rider = DeliveryPerson.query.get(order.delivery_person_id)
    
    # GET ISSUES WITH MESSAGES - THIS IS THE KEY PART
    issues = Issue.query.filter_by(order_id=order_id).order_by(Issue.created_at.desc()).all()
    
    # For each issue, fetch its messages
    for issue in issues:
        issue.messages_list = IssueMessage.query.filter_by(issue_id=issue.id).order_by(IssueMessage.created_at.asc()).all()
    
    # Debug print to verify
    print(f"🔍 Found {len(issues)} issues for order #{order_id}")
    for issue in issues:
        print(f"   Issue #{issue.id}: status={issue.status}, messages={len(issue.messages_list)}")
    
    return render_template('order_details.html',
                         order=order,
                         order_items=order_items,
                         tracking_updates=tracking_updates,
                         delivery_assignment=delivery_assignment,
                         issues=issues,  # Pass issues to template
                         rider=rider,
                         user=user)




@app.route('/api/seller/messages/conversations')
@login_required
def api_seller_conversations():
    """Get all conversations for seller (including admin)"""
    try:
        seller_id = session['user_id']
        
        # Get all conversations where seller is participant
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == seller_id, Conversation.participant1_type == 'seller'),
                db.and_(Conversation.participant2_id == seller_id, Conversation.participant2_type == 'seller')
            )
        ).order_by(Conversation.updated_at.desc()).all()
        
        result = []
        for conv in conversations:
            # Determine other participant
            if conv.participant1_id == seller_id and conv.participant1_type == 'seller':
                other_id = conv.participant2_id
                other_type = conv.participant2_type
            else:
                other_id = conv.participant1_id
                other_type = conv.participant1_type
            
            # Get other user details
            other_user = None
            other_name = 'Unknown'
            other_email = ''
            other_phone = ''
            
            if other_type == 'buyer':
                other_user = User.query.get(other_id)
                if other_user:
                    other_name = other_user.fullname or 'Buyer'
                    other_email = other_user.email or ''
                    other_phone = other_user.phone or ''
            elif other_type == 'admin':
                other_user = User.query.get(other_id)
                if other_user:
                    other_name = 'Admin Support'
                    other_email = other_user.email or ''
                    other_phone = other_user.phone or ''
            
            # Get last message
            last_message = Message.query.filter_by(conversation_id=conv.id).order_by(Message.created_at.desc()).first()
            
            # Get unread count
            unread_count = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != seller_id,
                Message.is_read == False
            ).count()
            
            # Get product details if any
            product = None
            if conv.product_id:
                product = Product.query.get(conv.product_id)
            
            # Get order details if any
            order = None
            if conv.order_id:
                order = Order.query.get(conv.order_id)
            
            result.append({
                'id': conv.id,
                'type': conv.conversation_type,
                'other_participant': {
                    'id': other_id,
                    'name': other_name,
                    'type': other_type,
                    'email': other_email,
                    'phone': other_phone
                },
                'last_message': {
                    'id': last_message.id,
                    'content': last_message.content[:50] + '...' if last_message and len(last_message.content) > 50 else (last_message.content if last_message else ''),
                    'created_at': last_message.created_at.isoformat() if last_message else None,
                    'sender_id': last_message.sender_id if last_message else None
                } if last_message else None,
                'unread_count': unread_count,
                'product': {
                    'id': product.id,
                    'name': product.name,
                    'price': float(product.price),
                    'image': product.image
                } if product else None,
                'order': {
                    'id': order.id,
                    'total': float(order.total_amount) if order and order.total_amount else 0,
                    'status': order.status if order else None
                } if order else None,
                'created_at': conv.created_at.isoformat() if conv.created_at else None,
                'updated_at': conv.updated_at.isoformat() if conv.updated_at else None
            })
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        print(f"Error in api_seller_conversations: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500       





@app.route('/products')
@app.route('/products/<category>')
def products(category=None):
    from sqlalchemy.orm import joinedload
    
    # Get filter parameters
    search = request.args.get('search', '')
    sort = request.args.get('sort', 'newest')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Base query - ONLY ACTIVE PRODUCTS
    query = Product.query.options(
        joinedload(Product.seller),
        joinedload(Product.reviews)
    ).filter_by(is_active=True)
    
    # Apply category filter
    if category and category != 'all':
        query = query.filter_by(category=category)
    
    # Apply search filter
    if search:
        query = query.filter(
            db.or_(
                Product.name.ilike(f'%{search}%'),
                Product.description.ilike(f'%{search}%')
            )
        )
    
    # Apply sorting
    if sort == 'price_low':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_high':
        query = query.order_by(Product.price.desc())
    elif sort == 'rating':
        query = query.order_by(Product.rating.desc())
    elif sort == 'popular':
        query = query.order_by(Product.sold_count.desc())
    else:  # newest
        query = query.order_by(Product.created_at.desc())
    
    # Get paginated results
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    items = pagination.items
    
    # Get wishlist IDs for logged in user
    wishlist_ids = []
    if 'user_id' in session:
        try:
            wishlist = Wishlist.query.filter_by(user_id=session['user_id']).all()
            wishlist_ids = [item.product_id for item in wishlist]
        except Exception as e:
            print(f"Error getting wishlist: {e}")
            wishlist_ids = []
    
    return render_template('products.html', 
                         items=items, 
                         category=category or 'all',
                         pagination=pagination,
                         wishlist_ids=wishlist_ids,
                         search=search,
                         sort=sort)




@app.route('/')
def home():
    """Home page with products"""
    try:
        print("="*50)
        print("🔍 HOME ROUTE CALLED")
        
        from sqlalchemy import func
        
        # Get stats
        total_users = User.query.count()
        total_products = Product.query.filter(Product.is_active == True).count()
        active_sellers = User.query.filter_by(user_type='seller', is_active=True).count()
        
        print(f"📊 Stats - Users: {total_users}, Products: {total_products}, Sellers: {active_sellers}")
        
        # Get trending products
        trending_products = Product.query.filter(
            Product.is_active == True,
            Product.stock > 0
        ).order_by(Product.sold_count.desc()).limit(8).all()
        
        print(f"🔥 Trending products found: {len(trending_products)}")
        
        # Get new arrivals
        new_arrivals = Product.query.filter(
            Product.is_active == True,
            Product.stock > 0
        ).order_by(Product.created_at.desc()).limit(8).all()
        
        print(f"🆕 New arrivals found: {len(new_arrivals)}")
        
        # Get recommended products
        recommended_products = Product.query.filter(
            Product.is_active == True,
            Product.stock > 0
        ).order_by(func.random()).limit(8).all()
        
        print(f"👍 Recommended products found: {len(recommended_products)}")
        
        # Get category counts
        category_counts = {}
        categories = [
            'textbooks', 'study_guides', 'stationery', 'calculators', 'lab_coats',
            'laptops', 'phones', 'tablets', 'headphones', 'chargers',
            'mens_clothing', 'womens_clothing', 'shoes', 'bags',
            'bedding', 'furniture', 'kitchen', 'storage',
            'sports_gear', 'gym_equipment',
            'gaming', 'music',
            'secondhand_books', 'secondhand_electronics', 'secondhand_furniture'
        ]
        
        for cat in categories:
            count = Product.query.filter(
                Product.category == cat,
                Product.is_active == True,
                Product.stock > 0
            ).count()
            category_counts[cat] = count
            print(f"📁 Category {cat}: {count}")
        
        stats = {
            'total_users': total_users,
            'total_products': total_products,
            'active_sellers': active_sellers
        }
        
        print("✅ Home route completed successfully")
        print("="*50)
        
        return render_template('index.html',
                             trending_products=trending_products,
                             new_arrivals=new_arrivals,
                             recommended_products=recommended_products,
                             category_counts=category_counts,
                             stats=stats)
    
    except Exception as e:
        print(f"❌ ERROR in home route: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"<h1>Error in home route</h1><pre>{traceback.format_exc()}</pre>", 500





@app.route('/start-conversation/<int:product_id>')
@login_required
def start_product_conversation(product_id):
    """Start a conversation with a seller about a product"""
    user = get_current_user()
    
    # Only buyers can message sellers
    if user.user_type != 'buyer':
        flash('Only buyers can message sellers.', 'danger')
        return redirect(url_for('product_detail', product_id=product_id))
    
    # Get the product
    product = Product.query.get_or_404(product_id)
    seller_id = product.seller_id
    
    # Don't allow messaging yourself
    if user.id == seller_id:
        flash('You cannot message yourself.', 'warning')
        return redirect(url_for('product_detail', product_id=product_id))
    
    # Check if conversation already exists
    existing = Conversation.query.filter(
        db.or_(
            db.and_(
                Conversation.participant1_id == user.id,
                Conversation.participant1_type == 'buyer',
                Conversation.participant2_id == seller_id,
                Conversation.participant2_type == 'seller',
                Conversation.product_id == product_id
            ),
            db.and_(
                Conversation.participant1_id == seller_id,
                Conversation.participant1_type == 'seller',
                Conversation.participant2_id == user.id,
                Conversation.participant2_type == 'buyer',
                Conversation.product_id == product_id
            )
        )
    ).first()
    
    if existing:
        # Conversation exists, redirect to it
        return redirect(url_for('chat', conversation_id=existing.id))
    
    try:
        # Create new conversation
        conversation = Conversation(
            participant1_id=user.id,
            participant1_type='buyer',
            participant2_id=seller_id,
            participant2_type='seller',
            conversation_type='buyer-seller',
            product_id=product_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.session.add(conversation)
        db.session.flush()
        
        # Create initial message
        initial_message = Message(
            conversation_id=conversation.id,
            sender_id=user.id,
            sender_type='buyer',
            content=f"Hi, I'm interested in your product: {product.name}",
            message_type='text',
            created_at=datetime.utcnow()
        )
        db.session.add(initial_message)
        
        # Update conversation with last message
        conversation.last_message_id = initial_message.id
        conversation.last_message_at = datetime.utcnow()
        
        db.session.commit()
        
        flash('Conversation started! You can now message the seller.', 'success')
        return redirect(url_for('chat', conversation_id=conversation.id))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error starting conversation: {e}")
        traceback.print_exc()
        flash('Error starting conversation. Please try again.', 'danger')
        return redirect(url_for('product_detail', product_id=product_id))


# Get conversations for current user
@app.route('/api/messages/conversations')
@login_required
def get_conversations():
    # Your existing code
    pass

# Get messages for a conversation
@app.route('/api/messages/conversations/<int:conversation_id>/messages')
@login_required
def get_conversation_messages(conversation_id):
    # Your existing code
    pass

# Send a message
@app.route('/api/messages/send', methods=['POST'])
@login_required
def send_message():
    # Your existing code
    pass

# Mark conversation as read
@app.route('/api/messages/conversations/<int:conversation_id>/read', methods=['PUT'])
@login_required
def mark_conversation_read(conversation_id):
    # Your existing code
    pass

# For sellers
@app.route('/api/seller/messages/conversations')
@login_required
def seller_conversations():
    # Your existing code
    pass



@app.route('/reset-subscription')
@login_required
def reset_subscription():
    """Reset user's subscription for testing - REMOVE THIS AFTER TESTING"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Only sellers can reset subscriptions', 'danger')
        return redirect(url_for('dashboard'))
    
    # Reset subscription
    user.subscription_tier = None
    user.subscription_expiry = None
    db.session.commit()
    
    flash('✅ Your subscription has been reset! You can now try the free trial.', 'success')
    return redirect(url_for('seller_subscription'))


# ==================== SELLER SUBSCRIPTION ROUTES ====================



@app.route('/force-reset-subscription')
@login_required
def force_reset_subscription():
    """Force reset user's subscription - This will make the free trial available"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Only sellers can reset subscriptions', 'danger')
        return redirect(url_for('dashboard'))
    
    # Store old values for display
    old_tier = user.subscription_tier
    old_expiry = user.subscription_expiry
    
    # Force reset to None
    user.subscription_tier = None
    user.subscription_expiry = None
    
    db.session.commit()
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Subscription Reset Complete</title>
        <style>
            body {{
                font-family: 'Segoe UI', Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                padding: 20px;
            }}
            .card {{
                background: white;
                border-radius: 20px;
                padding: 40px;
                max-width: 500px;
                text-align: center;
                box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            }}
            .success-icon {{
                width: 80px;
                height: 80px;
                background: #10b981;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 20px;
            }}
            .success-icon i {{
                font-size: 40px;
                color: white;
            }}
            h1 {{ color: #1f2937; margin-bottom: 10px; }}
            .info {{ background: #f3f4f6; padding: 15px; border-radius: 10px; margin: 20px 0; text-align: left; }}
            .info p {{ margin: 8px 0; }}
            .old {{ color: #ef4444; }}
            .new {{ color: #10b981; font-weight: bold; }}
            .btn {{
                display: inline-block;
                padding: 12px 24px;
                background: #ff6b00;
                color: white;
                text-decoration: none;
                border-radius: 30px;
                margin: 10px;
                transition: all 0.2s;
            }}
            .btn:hover {{ transform: translateY(-2px); background: #e65100; }}
            .btn-secondary {{
                background: #000080;
            }}
            .btn-secondary:hover {{ background: #000066; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="success-icon">
                <i class="fas fa-check"></i>
            </div>
            <h1>✅ Subscription Reset!</h1>
            <p>Your account has been successfully reset to a new seller status.</p>
            
            <div class="info">
                <p><strong>Before:</strong></p>
                <p>• Subscription Tier: <span class="old">{{ old_tier }}</span></p>
                <p>• Subscription Expiry: <span class="old">{{ old_expiry or 'None' }}</span></p>
                <hr>
                <p><strong>After:</strong></p>
                <p>• Subscription Tier: <span class="new">None</span></p>
                <p>• Subscription Expiry: <span class="new">None</span></p>
            </div>
            
            <p>You can now start the free trial!</p>
            
            <a href="/seller/subscription" class="btn">Go to Subscription Page</a>
            <a href="/debug-status" class="btn btn-secondary">Check Status</a>
        </div>
        
        <script src="https://kit.fontawesome.com/your-code.js"></script>
    </body>
    </html>
    """




@app.route('/fix-all-sellers')
@admin_required
def fix_all_sellers():
    """Fix all sellers to have subscription_tier = None if they have no expiry"""
    sellers = User.query.filter_by(user_type='seller').all()
    
    fixed_count = 0
    results = []
    
    for seller in sellers:
        old_tier = seller.subscription_tier
        # If seller has no active subscription (expiry is None or expired), reset to None
        if not seller.subscription_expiry or seller.subscription_expiry < datetime.utcnow():
            seller.subscription_tier = None
            seller.subscription_expiry = None
            fixed_count += 1
            results.append(f"User {seller.id} ({seller.fullname}): {old_tier} → None")
    
    db.session.commit()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Fixed All Sellers</title>
        <style>
            body {{ font-family: Arial; padding: 40px; background: #f5f5f5; }}
            .card {{ background: white; border-radius: 12px; padding: 30px; max-width: 800px; margin: 0 auto; }}
            .success {{ color: green; }}
            ul {{ background: #f8fafc; padding: 20px; border-radius: 8px; }}
            li {{ margin: 5px 0; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1 class="success">✅ Fixed {fixed_count} Sellers</h1>
            <p>All sellers with expired or no active subscriptions have been reset to new seller status.</p>
            <h3>Changes Made:</h3>
            <ul>
                {"".join(f"<li>{r}</li>" for r in results[:20])}
                {"<li>... and more</li>" if len(results) > 20 else ""}
            </ul>
            <a href="/admin/sellers" style="background: #ff6b00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; margin-top: 20px;">Go to Sellers</a>
        </div>
    </body>
    </html>
    """
    
    return html








# Update the add_product route to check product limits
@app.route('/seller/products/add', methods=['GET', 'POST'])
@login_required
def add_product():
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied. Seller account required.', 'danger')
        return redirect(url_for('products'))
    
    # Check subscription and product limits
    if not has_active_subscription(user):
        flash('Please subscribe to a plan to add products.', 'info')
        return redirect(url_for('seller_subscription'))
    
    # Get current product count
    current_products = Product.query.filter_by(seller_id=user.id, is_active=True).count()
    
    # Define product limits per plan
    plan_limits = {
        'starter': 4,
        'basic': 25,
        'premium': 100
    }
    
    max_products = plan_limits.get(user.subscription_tier, 0)
    
    if current_products >= max_products:
        flash(f'You have reached your plan limit of {max_products} products. Please upgrade to add more.', 'warning')
        return redirect(url_for('seller_subscription'))
    
    if request.method == 'POST':
        # ... rest of your add_product code ...
        try:
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', '').strip()
            category = request.form.get('category', '').strip()
            stock = request.form.get('stock', '1').strip()
            brand = request.form.get('brand', '').strip()
            condition = request.form.get('condition', 'new')
            
            if not all([name, description, price, category]):
                flash('Please fill in all required fields: Name, Description, Price, and Category.', 'danger')
                return render_template('add_product.html')
            
            image_file = request.files.get('image')
            image_filename = None
            
            if image_file and image_file.filename:
                if allowed_file(image_file.filename):
                    ensure_upload_folder()
                    filename = secure_filename(image_file.filename)
                    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
                    image_filename = timestamp + filename
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                    image_file.save(image_path)
                else:
                    flash('Please upload JPG, PNG, or GIF images only.', 'danger')
                    return render_template('add_product.html')
            
            new_product = Product(
                name=name,
                description=description,
                price=float(price),
                category=category,
                stock=int(stock) if stock else 1,
                image=image_filename,
                brand=brand if brand else None,
                condition=condition,
                seller_id=user.id,
                is_active=True
            )
            
            db.session.add(new_product)
            db.session.commit()
            
            flash('🎉 Product added successfully!', 'success')
            return redirect(url_for('manage_products'))
            
        except ValueError as e:
            flash('Please enter valid price and stock values.', 'danger')
            return render_template('add_product.html')
        except Exception as e:
            db.session.rollback()
            flash('Error adding product. Please try again.', 'danger')
            return render_template('add_product.html')
    
    return render_template('add_product.html')


# ==================== UCU DATABASE FUNCTIONS ====================
def generate_complete_ucu_data():
    """Generate both students and staff data - WITH DUPLICATE CHECKING"""
    
    students = [
        ("B22564", "Okello John", "Computer Science", 3, "Faculty of Science and Technology"),
        ("B22789", "Nabatanzi Sarah", "Business Administration", 2, "Faculty of Business"),
        ("B22134", "Kato David", "Law", 4, "Faculty of Law"),
        ("B22345", "Namugenyi Grace", "Medicine", 3, "Faculty of Health Sciences"),
        ("B22678", "Mukasa Peter", "Engineering", 2, "Faculty of Engineering"),
        ("B22890", "Nakato Mary", "Education", 1, "Faculty of Education"),
        ("B22123", "Ssali James", "Computer Science", 4, "Faculty of Science and Technology"),
        ("B22456", "Achieng Brenda", "Business", 2, "Faculty of Business"),
        ("B22777", "Wasswa Robert", "Law", 3, "Faculty of Law"),
        ("B22333", "Kizza Sarah", "Medicine", 2, "Faculty of Health Sciences"),
        ("B22901", "Mutesi Grace", "Computer Science", 1, "Faculty of Science and Technology"),
        ("B22555", "Ssenyonga David", "Engineering", 3, "Faculty of Engineering"),
        ("B22222", "Nakamya Patience", "Business", 4, "Faculty of Business"),
        ("B22444", "Okot Moses", "Education", 2, "Faculty of Education"),
        ("B22666", "Amoding Esther", "Law", 1, "Faculty of Law"),
        ("B22888", "Wasswa Henry", "Medicine", 4, "Faculty of Health Sciences"),
        ("B22111", "Namutebi Ruth", "Computer Science", 2, "Faculty of Science and Technology"),
        ("B22333", "Ssekitoleko Robert", "Engineering", 1, "Faculty of Engineering"),
        ("B22500", "Ndagire Rose", "Business", 3, "Faculty of Business"),
        ("B22789", "Mukasa James", "Education", 3, "Faculty of Education"),
        ("A22345", "Akello Patricia", "Law", 2, "Faculty of Law"),
        ("A22678", "Aol Grace", "Medicine", 1, "Faculty of Health Sciences"),
        ("A22890", "Atim Brenda", "Computer Science", 3, "Faculty of Science and Technology"),
        ("A22123", "Amongin Sarah", "Engineering", 4, "Faculty of Engineering"),

        ("B24001", "Abenaitwe Patricia", "Computer Science", 2, "Faculty of Science and Technology"),
        ("B24002", "Baguma Richard", "Computer Science", 3, "Faculty of Science and Technology"),
        ("B24003", "Chemutai Mercy", "Computer Science", 1, "Faculty of Science and Technology"),
        ("B24004", "Ddamulira Matthias", "Computer Science", 4, "Faculty of Science and Technology"),
        ("B24005", "Ekunait Rose", "Computer Science", 2, "Faculty of Science and Technology"),
        ("B24006", "Feni Catherine", "Computer Science", 3, "Faculty of Science and Technology"),
        ("B24007", "Gumisiriza Daniel", "Computer Science", 1, "Faculty of Science and Technology"),
        ("B24008", "Habumugisha Robert", "Computer Science", 4, "Faculty of Science and Technology"),
        ("B24009", "Irankunda Grace", "Computer Science", 2, "Faculty of Science and Technology"),
        ("B24010", "Jovita Akello", "Computer Science", 3, "Faculty of Science and Technology"),
        ("B24011", "Kabagambe Moses", "Computer Science", 1, "Faculty of Science and Technology"),
        ("B24012", "Laker Florence", "Computer Science", 4, "Faculty of Science and Technology"),
        ("B24013", "Magezi Isaac", "Computer Science", 2, "Faculty of Science and Technology"),
        ("B24014", "Nabifo Esther", "Computer Science", 3, "Faculty of Science and Technology"),
        ("B24015", "Ocen Patrick", "Computer Science", 1, "Faculty of Science and Technology"),
        ("B24016", "Aanyu Harriet", "Business Administration", 3, "Faculty of Business"),
        ("B24017", "Bamwine Allan", "Business Administration", 2, "Faculty of Business"),
        ("B24018", "Chelangat Sharon", "Business Administration", 4, "Faculty of Business"),
        ("B24019", "Driciru Brenda", "Business Administration", 1, "Faculty of Business"),
        ("B24020", "Egesa Brian", "Business Administration", 3, "Faculty of Business"),
        ("B24021", "Friday Ben", "Business Administration", 2, "Faculty of Business"),
        ("B24022", "Gloria Akello", "Business Administration", 4, "Faculty of Business"),
        ("B24023", "Habert Tumusiime", "Business Administration", 1, "Faculty of Business"),
        ("B24024", "Irene Nakato", "Business Administration", 3, "Faculty of Business"),
        ("B24025", "Julius Byamukama", "Business Administration", 2, "Faculty of Business"),
        ("B24026", "Kevin Asiimwe", "Business Administration", 4, "Faculty of Business"),
        ("B24027", "Loyce Amongin", "Business Administration", 1, "Faculty of Business"),
        ("B24028", "Moses Turyatemba", "Business Administration", 3, "Faculty of Business"),
        ("B24029", "Nina Kyomugisha", "Business Administration", 2, "Faculty of Business"),
        ("B24030", "Opio Denis", "Business Administration", 4, "Faculty of Business"),
        ("A24031", "Agnes Nabatanzi", "Law", 2, "Faculty of Law"),
        ("B24032", "Busingye Phiona", "Law", 3, "Faculty of Law"),
        ("B24033", "Christopher Mukiibi", "Law", 1, "Faculty of Law"),
        ("A24034", "Dorothy Namutebi", "Law", 4, "Faculty of Law"),
        ("B24035", "Emmanuel Ssekandi", "Law", 2, "Faculty of Law"),
        ("B24036", "Florence Nambi", "Law", 3, "Faculty of Law"),
        ("A24037", "Geoffrey Kazibwe", "Law", 1, "Faculty of Law"),
        ("B24038", "Hellen Akurut", "Law", 4, "Faculty of Law"),
        ("B24039", "Isaac Muwanga", "Law", 2, "Faculty of Law"),
        ("B24040", "Jackline Nalule", "Law", 3, "Faculty of Law"),
        ("B24041", "Kenneth Mugisha", "Medicine", 3, "Faculty of Health Sciences"),
        ("B24042", "Lillian Nankinga", "Medicine", 2, "Faculty of Health Sciences"),
        ("B24043", "Martin Ssenyonga", "Medicine", 4, "Faculty of Health Sciences"),
        ("B24044", "Nelson Wasswa", "Medicine", 1, "Faculty of Health Sciences"),
        ("B24045", "Olivia Nakamya", "Medicine", 3, "Faculty of Health Sciences"),
        ("B24046", "Paul Ssebulime", "Medicine", 2, "Faculty of Health Sciences"),
        ("B24047", "Queen Nansubuga", "Medicine", 4, "Faculty of Health Sciences"),
        ("B24048", "Richard Kato", "Medicine", 1, "Faculty of Health Sciences"),
        ("B24049", "Sarah Nakanwagi", "Medicine", 3, "Faculty of Health Sciences"),
        ("B24050", "Tom Mutyaba", "Medicine", 2, "Faculty of Health Sciences"),
        ("B24051", "Umar Ssekandi", "Engineering", 3, "Faculty of Engineering"),
        ("B24052", "Violet Nakaweesi", "Engineering", 2, "Faculty of Engineering"),
        ("B24053", "Wilson Mugerwa", "Engineering", 4, "Faculty of Engineering"),
        ("B24054", "Xavier Ssentongo", "Engineering", 1, "Faculty of Engineering"),
        ("B24055", "Yvonne Nankinga", "Engineering", 3, "Faculty of Engineering"),
        ("B24056", "Zachary Okello", "Engineering", 2, "Faculty of Engineering"),
        ("B24057", "Aisha Nalwoga", "Engineering", 4, "Faculty of Engineering"),
        ("B24058", "Benon Kaggwa", "Engineering", 1, "Faculty of Engineering"),
        ("B24059", "Cissy Nakato", "Engineering", 3, "Faculty of Engineering"),
        ("B24060", "Davis Ssemakula", "Engineering", 2, "Faculty of Engineering"),
        ("B24061", "Edith Nambozo", "Education", 2, "Faculty of Education"),
        ("B24062", "Francis Ssali", "Education", 3, "Faculty of Education"),
        ("B24063", "Grace Nambi", "Education", 1, "Faculty of Education"),
        ("B24064", "Henry Kizza", "Education", 4, "Faculty of Education"),
        ("B24065", "Irene Nakibuuka", "Education", 2, "Faculty of Education"),
        ("B24066", "James Mutebi", "Education", 3, "Faculty of Education"),
        ("B24067", "Katherine Namazzi", "Education", 1, "Faculty of Education"),
        ("B24068", "Lawrence Ssenyonga", "Education", 4, "Faculty of Education"),
        ("B24069", "Margaret Nalubega", "Education", 2, "Faculty of Education"),
        ("B24070", "Nicholas Ssekandi", "Education", 3, "Faculty of Education"),
        ("B24071", "Oliver Nakanjako", "Social Sciences", 3, "Faculty of Social Sciences"),
        ("B24072", "Peter Kasule", "Social Sciences", 2, "Faculty of Social Sciences"),
        ("B24073", "Rebecca Nansubuga", "Social Sciences", 4, "Faculty of Social Sciences"),
        ("B24074", "Samuel Kato", "Social Sciences", 1, "Faculty of Social Sciences"),
        ("B24075", "Theresa Nankinga", "Social Sciences", 3, "Faculty of Social Sciences"),
        ("B24076", "Ursula Nalule", "Nursing", 2, "Faculty of Health Sciences"),
        ("B24077", "Vincent Ssempala", "Nursing", 3, "Faculty of Health Sciences"),
        ("B24078", "Winnie Nansamba", "Nursing", 1, "Faculty of Health Sciences"),
        ("B24079", "Xerxes Mukasa", "Nursing", 4, "Faculty of Health Sciences"),
        ("B24080", "Yusuf Ssekyanzi", "Nursing", 2, "Faculty of Health Sciences"),
        ("B24081", "Zainab Nakitende", "Pharmacy", 3, "Faculty of Health Sciences"),
        ("B24082", "Abel Muwonge", "Pharmacy", 2, "Faculty of Health Sciences"),
        ("B24083", "Beatrice Nandawula", "Pharmacy", 4, "Faculty of Health Sciences"),
        ("B24084", "Charles Lwanga", "Pharmacy", 1, "Faculty of Health Sciences"),
        ("B24085", "Diana Nabatanzi", "Pharmacy", 3, "Faculty of Health Sciences"),

    







    ("abenaitwe", "Dr. Patricia Abenaitwe", "Computer Science", "Senior Lecturer"),
    ("baguma", "Prof. Richard Baguma", "Business", "Professor"),
    ("chemutai", "Dr. Mercy Chemutai", "Medicine", "Consultant"),
    ("ddamulira", "Mr. Matthias Ddamulira", "Engineering", "Lecturer"),
    ("ekunait", "Ms. Rose Ekunait", "Law", "Senior Lecturer"),
    ("feni", "Dr. Catherine Feni", "Education", "Associate Professor"),
    ("gumisiriza", "Prof. Daniel Gumisiriza", "Social Sciences", "Professor"),
    ("habumugisha", "Dr. Robert Habumugisha", "Pharmacy", "Senior Lecturer"),
    ("irankunda", "Ms. Grace Irankunda", "Nursing", "Lecturer"),
    ("jovita", "Dr. Akello Jovita", "Medicine", "Senior Consultant"),
    ("kabagambe", "Mr. Moses Kabagambe", "Engineering", "Senior Lecturer"),
    ("laker", "Prof. Florence Laker", "Business", "Professor"),
    ("magezi", "Dr. Isaac Magezi", "Computer Science", "Associate Professor"),
    ("nabifo", "Ms. Esther Nabifo", "Law", "Lecturer"),
    ("ocen", "Dr. Patrick Ocen", "Education", "Senior Lecturer"),


    ]
    
    staff = [
        ("okello", "Dr. Peter Okello", "Computer Science", "Senior Lecturer"),
        ("nakato", "Prof. Grace Nakato", "Law", "Professor"),
        ("ssenyonjo", "Dr. James Ssenyonjo", "Business", "Associate Professor"),
        ("nabatanzi", "Ms. Sarah Nabatanzi", "Education", "Lecturer"),
        ("kato", "Mr. David Kato", "Engineering", "Senior Lecturer"),
        ("namugenyi", "Dr. Maria Namugenyi", "Medicine", "Lecturer"),
        ("mukasa", "Prof. Robert Mukasa", "Computer Science", "Professor"),
        ("akello", "Dr. Beatrice Akello", "Business", "Senior Lecturer"),
        ("tumusiime", "Mr. Andrew Tumusiime", "Law", "Lecturer"),
        ("nakimuli", "Ms. Florence Nakimuli", "Education", "Senior Lecturer"),
        ("ssenoga", "Dr. John Ssenoga", "Engineering", "Associate Professor"),
        ("namutebi", "Prof. Ruth Namutebi", "Medicine", "Professor"),
        ("wasswa", "Dr. Henry Wasswa", "Computer Science", "Lecturer"),
        ("nakayingo", "Ms. Grace Nakayingo", "Business", "Senior Lecturer"),
        ("muhumuza", "Mr. Robert Muhumuza", "Law", "Associate Professor"),
    ]
    
    count = 0
    
    # Add students - check if email already exists
    for student_num, name, dept, year, faculty in students:
        email = f"{student_num}@students.ucu.ac.ug"
        if not UCUEmail.query.filter_by(email=email).first():
            student = UCUEmail(
                email=email,
                full_name=name,
                user_type='student',
                student_number=student_num,
                department=dept,
                year_of_study=year,
                faculty=faculty,
                is_active=True
            )
            db.session.add(student)
            count += 1
            print(f"➕ Added student: {email}")
        else:
            print(f"⏭️ Student already exists: {email}")
    
    # Add staff - check if email already exists
    for email_prefix, name, dept, title in staff:
        email = f"{email_prefix}@ucu.ac.ug"
        if not UCUEmail.query.filter_by(email=email).first():
            staff_member = UCUEmail(
                email=email,
                full_name=name,
                user_type='staff',
                department=dept,
                faculty=f"Faculty of {dept}",
                staff_title=title,
                is_active=True
            )
            db.session.add(staff_member)
            count += 1
            print(f"➕ Added staff: {email}")
        else:
            print(f"⏭️ Staff already exists: {email}")
    
    db.session.commit()
    print(f"✅ Successfully added {count} new UCU emails to database")
    return count

def validate_student_email(email):
    pattern = r'^[AB]\d{5}@students\.ucu\.ac\.ug$'
    return re.match(pattern, email, re.IGNORECASE) is not None

def validate_staff_email(email):
    pattern = r'^[a-z]+@ucu\.ac\.ug$'
    return re.match(pattern, email, re.IGNORECASE) is not None

def is_valid_ucu_email(email):
    return UCUEmail.query.filter_by(email=email, is_active=True).first() is not None

def import_ucu_emails_from_csv(file):
    import csv
    from io import TextIOWrapper
    
    count = 0
    csv_file = TextIOWrapper(file, encoding='utf-8')
    reader = csv.DictReader(csv_file)
    
    for row in reader:
        email = row.get('email')
        if email and not UCUEmail.query.filter_by(email=email).first():
            ucu_email = UCUEmail(
                email=email,
                full_name=row.get('full_name', ''),
                user_type=row.get('user_type', 'student'),
                student_number=row.get('student_number'),
                department=row.get('department', '')
            )
            db.session.add(ucu_email)
            count += 1
    
    db.session.commit()
    return count





@app.route('/api/admin/users/<int:user_id>')
@admin_required
def api_admin_user_profile(user_id):
    """Get user profile for modal (for buyers)"""
    try:
        user = User.query.get_or_404(user_id)
        
        return jsonify({
            'id': user.id,
            'fullname': user.fullname,
            'email': user.email,
            'phone': user.phone,
            'location': user.location,
            'delivery_address': user.delivery_address,
            'user_type': user.user_type,
            'joined': user.created_at.strftime('%d %b %Y') if user.created_at else 'N/A'
        })
    except Exception as e:
        print(f"Error getting user profile: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500






# ============ SELLER MESSAGES ROUTE ============
@app.route('/seller/messages')
@login_required
def seller_messages():
    """Seller messages dashboard"""
    # Check if user is seller
    user = get_current_user()
    if user.user_type != 'seller':
        flash('Access denied. Seller account required.', 'danger')
        return redirect(url_for('products'))
    
    return render_template('seller_messages.html',
                         session=session,
                         cart_count=get_cart_count())

# ============ ADMIN MESSAGES ROUTE ============
@app.route('/admin/messages')
@login_required
def admin_messages():
    """Admin messages dashboard"""
    # Check if user is admin
    user = get_current_user()
    if user.user_type != 'admin':
        flash('Access denied. Admin account required.', 'danger')
        return redirect(url_for('home'))
    
    return render_template('admin_messages.html',
                         session=session,
                         cart_count=get_cart_count())



@app.route('/api/admin/users/<int:user_id>/toggle-status', methods=['POST'])
@admin_required
def admin_toggle_user_status(user_id):
    """Toggle user active status"""
    try:
        data = request.get_json()
        user = User.query.get_or_404(user_id)
        
        # Don't allow deactivating yourself
        if user.id == session['user_id']:
            return jsonify({'success': False, 'message': 'You cannot deactivate your own account'}), 400
        
        action = data.get('action', '')
        
        if action == 'activate':
            user.is_active = True
            message = 'activated'
        elif action == 'deactivate':
            user.is_active = False
            message = 'deactivated'
        else:
            # Toggle if no action specified
            user.is_active = not user.is_active
            message = 'activated' if user.is_active else 'deactivated'
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'User {message} successfully',
            'is_active': user.is_active
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error toggling user status: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500





@app.route('/api/admin/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def admin_reset_user_password(user_id):
    """Reset user password and return new password"""
    try:
        user = User.query.get_or_404(user_id)
        
        # Generate random password
        import secrets
        new_password = secrets.token_urlsafe(8)
        
        # Hash and save
        user.password = generate_password_hash(new_password)
        user.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Password reset successfully',
            'new_password': new_password
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error resetting password: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/users/export', methods=['GET'])
@admin_required
def admin_export_users():
    """Export users to CSV"""
    try:
        # Get filter parameters
        search = request.args.get('search', '')
        filter_type = request.args.get('filter', 'all')
        
        # Build query
        query = User.query
        
        if search:
            query = query.filter(
                db.or_(
                    User.fullname.ilike(f'%{search}%'),
                    User.email.ilike(f'%{search}%'),
                    User.phone.ilike(f'%{search}%')
                )
            )
        
        if filter_type == 'active':
            query = query.filter_by(is_active=True)
        elif filter_type == 'inactive':
            query = query.filter_by(is_active=False)
        elif filter_type != 'all':
            query = query.filter_by(user_type=filter_type)
        
        users = query.order_by(User.created_at.desc()).all()
        
        # Create CSV
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow(['ID', 'Name', 'Email', 'Phone', 'Type', 'Status', 'Location', 'Joined'])
        
        # Write data
        for user in users:
            writer.writerow([
                user.id,
                user.fullname,
                user.email,
                user.phone or 'N/A',
                user.user_type,
                'Active' if user.is_active else 'Inactive',
                user.location or 'N/A',
                user.created_at.strftime('%Y-%m-%d %H:%M') if user.created_at else 'N/A'
            ])
        
        # Create response
        csv_content = output.getvalue()
        output.close()
        
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=users_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error exporting users: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500          



# ==================== REPORT GENERATION FUNCTIONS ====================
def generate_sales_report(filters, format_type):
    """Generate sales report CSV"""
    try:
        query = Order.query
        if filters:
            query = query.filter(*filters)
        orders = query.order_by(Order.created_at.desc()).all()
        
        output = BytesIO()
        writer = csv.writer(output)
        
        writer.writerow(['Order ID', 'Date', 'Customer', 'Amount (UGX)', 'Status', 
                         'Payment Method', 'Items', 'Delivery Location'])
        
        for order in orders:
            writer.writerow([
                order.id,
                order.created_at.strftime('%Y-%m-%d %H:%M'),
                order.user.fullname if order.user else 'Guest',
                f"{order.total_amount:,.0f}",
                order.status.title() if order.status else 'Pending',
                order.payment_method or 'N/A',
                len(order.order_items),
                order.delivery_address or 'N/A'
            ])
        
        total_revenue = sum(o.total_amount for o in orders)
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Orders', len(orders)])
        writer.writerow(['Total Revenue', f'UGX {total_revenue:,.0f}'])
        writer.writerow(['Average Order Value', f'UGX {(total_revenue/len(orders)):,.0f}' if orders else 'UGX 0'])
        
        output.seek(0)
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'sales_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        print(f"Error in sales report: {str(e)}")
        raise e

def generate_users_report(filters, format_type):
    """Generate users report CSV"""
    try:
        users = User.query.order_by(User.created_at.desc()).all()
        
        output = BytesIO()
        writer = csv.writer(output)
        
        writer.writerow(['User ID', 'Name', 'Email', 'Type', 'Phone', 'Business', 
                         'Status', 'Joined Date'])
        
        for user in users:
            writer.writerow([
                user.id,
                user.fullname,
                user.email,
                user.user_type.title() if user.user_type else 'Buyer',
                user.phone or 'N/A',
                user.business_name or 'N/A',
                'Active' if user.is_active else 'Inactive',
                user.created_at.strftime('%Y-%m-%d') if user.created_at else 'N/A'
            ])
        
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Users', len(users)])
        writer.writerow(['Buyers', User.query.filter_by(user_type='buyer').count()])
        writer.writerow(['Sellers', User.query.filter_by(user_type='seller').count()])
        writer.writerow(['Staff', User.query.filter_by(user_type='staff').count()])
        writer.writerow(['Admins', User.query.filter_by(user_type='admin').count()])
        writer.writerow(['Active Users', User.query.filter_by(is_active=True).count()])
        writer.writerow(['Inactive Users', User.query.filter_by(is_active=False).count()])
        
        output.seek(0)
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'users_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        print(f"Error in users report: {str(e)}")
        raise e

def generate_products_report(filters, format_type):
    """Generate products report CSV"""
    try:
        products = Product.query.order_by(Product.created_at.desc()).all()
        
        output = BytesIO()
        writer = csv.writer(output)
        
        writer.writerow(['Product ID', 'Name', 'Seller', 'Category', 'Price (UGX)', 
                         'Stock', 'Sold', 'Status', 'Created Date'])
        
        for product in products:
            writer.writerow([
                product.id,
                product.name,
                product.seller.business_name or product.seller.fullname if product.seller else 'N/A',
                product.category.title() if product.category else 'General',
                f"{product.price:,.0f}",
                product.stock,
                product.sold_count or 0,
                'Active' if product.is_active else 'Inactive',
                product.created_at.strftime('%Y-%m-%d') if product.created_at else 'N/A'
            ])
        
        total_value = sum(p.price * p.stock for p in products)
        total_sold = sum(p.sold_count or 0 for p in products)
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Products', len(products)])
        writer.writerow(['Active Products', Product.query.filter_by(is_active=True).count()])
        writer.writerow(['Total Inventory Value', f'UGX {total_value:,.0f}'])
        writer.writerow(['Total Items Sold', total_sold])
        
        output.seek(0)
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'products_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        print(f"Error in products report: {str(e)}")
        raise e

def generate_sellers_report(filters, format_type):
    """Generate sellers report CSV"""
    try:
        sellers = User.query.filter_by(user_type='seller').order_by(User.created_at.desc()).all()
        
        output = BytesIO()
        writer = csv.writer(output)
        
        writer.writerow(['Seller ID', 'Name', 'Email', 'Business', 'Phone', 'NIN', 
                         'Products', 'Rating', 'Revenue', 'Status', 'Joined'])
        
        for seller in sellers:
            products_count = Product.query.filter_by(seller_id=seller.id).count()
            total_revenue = db.session.query(
                func.sum(OrderItem.price * OrderItem.quantity)
            ).join(Order).filter(
                OrderItem.seller_id == seller.id,
                Order.status == 'completed'
            ).scalar() or 0
            
            writer.writerow([
                seller.id,
                seller.fullname,
                seller.email,
                seller.business_name or 'N/A',
                seller.phone or 'N/A',
                seller.nin or 'N/A',
                products_count,
                f"{seller.seller_rating or 0:.1f}",
                f"{total_revenue:,.0f}",
                'Active' if seller.is_active else 'Inactive',
                seller.created_at.strftime('%Y-%m-%d') if seller.created_at else 'N/A'
            ])
        
        total_revenue_all = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            Order.status == 'completed'
        ).scalar() or 0
        
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Sellers', len(sellers)])
        writer.writerow(['Active Sellers', User.query.filter_by(user_type='seller', is_active=True).count()])
        writer.writerow(['Total Products Sold', db.session.query(func.sum(Product.sold_count)).scalar() or 0])
        writer.writerow(['Total Seller Revenue', f'UGX {total_revenue_all:,.0f}'])
        
        output.seek(0)
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'sellers_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        print(f"Error in sellers report: {str(e)}")
        raise e







@app.route('/admin/orders')
@admin_required
def admin_orders():
    """Admin orders management page"""
    user = get_current_user()
    
    # Get filter parameters
    status = request.args.get('status', '')
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Build base query
    query = Order.query.options(
        db.joinedload(Order.user),
        db.joinedload(Order.order_items).joinedload(OrderItem.product),
        db.joinedload(Order.order_items).joinedload(OrderItem.seller)
    )
    
    # Apply status filter
    if status:
        if status == 'out_for_delivery':
            # Include all variations of out for delivery
            query = query.filter(
                db.or_(
                    Order.status == 'out_for_delivery',
                    Order.status == 'shipped',
                    Order.status == 'in_transit'
                )
            )
        else:
            query = query.filter(Order.status == status)
    
    # Apply search filter
    if search_query:
        query = query.join(User).filter(
            db.or_(
                Order.id.ilike(f'%{search_query}%'),
                User.fullname.ilike(f'%{search_query}%'),
                User.email.ilike(f'%{search_query}%')
            )
        )
    
    # Get paginated results
    pagination = query.order_by(Order.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    orders = pagination.items
    
    # Calculate status counts for stats cards
    pending_count = Order.query.filter_by(status='pending').count()
    confirmed_count = Order.query.filter_by(status='confirmed').count()
    processing_count = Order.query.filter_by(status='processing').count()
    
    # OUT FOR DELIVERY COUNT - Include all variations
    out_for_delivery_count = Order.query.filter(
        db.or_(
            Order.status == 'out_for_delivery',
            Order.status == 'shipped',
            Order.status == 'in_transit'
        )
    ).count()
    
    delivered_count = Order.query.filter_by(status='delivered').count()
    completed_count = Order.query.filter_by(status='completed').count()
    cancelled_count = Order.query.filter_by(status='cancelled').count()
    
    # Issues count
    issue_count = Order.query.filter(
        db.or_(
            Order.status == 'issue',
            Order.status == 'issue_reported'
        )
    ).count()
    
    # Total orders
    total_orders = Order.query.count()
    
    # Debug prints
    print("="*50)
    print("🔍 ADMIN ORDERS PAGE")
    print(f"pending_count: {pending_count}")
    print(f"confirmed_count: {confirmed_count}")
    print(f"out_for_delivery_count: {out_for_delivery_count}")
    print(f"issue_count: {issue_count}")
    print(f"total_orders: {total_orders}")
    print("="*50)
    
    return render_template('admin_orders.html',
                         user=user,
                         orders=orders,
                         pagination=pagination,
                         pending_count=pending_count,
                         confirmed_count=confirmed_count,
                         processing_count=processing_count,
                         out_for_delivery_count=out_for_delivery_count,
                         delivered_count=delivered_count,
                         completed_count=completed_count,
                         cancelled_count=cancelled_count,
                         issue_count=issue_count,
                         total_orders=total_orders,
                         now=datetime.utcnow)


# Add this helper function to your app.py
def get_order_status_display(status):
    """Convert database status to user-friendly display text"""
    status_map = {
        'pending': 'Pending',
        'confirmed': 'Confirmed',
        'processing': 'Processing',
        'out_for_delivery': 'Out for Delivery',  # Changed from 'shipped'
        'delivered': 'Delivered',
        'completed': 'Completed',
        'cancelled': 'Cancelled'
    }
    return status_map.get(status, status.replace('_', ' ').title())







@app.route('/order/<int:order_id>/delivery-confirmation', methods=['GET', 'POST'])
@login_required
def delivery_confirmation(order_id):
    """Handle delivery confirmation from buyer"""
    order = Order.query.get_or_404(order_id)
    user = get_current_user()
    
    # Ensure only the buyer can confirm delivery
    if order.user_id != user.id:
        flash('You are not authorized to confirm delivery for this order.', 'danger')
        return redirect(url_for('order_details', order_id=order_id))
    
    # Check if order is in deliverable state
    if order.status not in ['shipped', 'out_for_delivery']:
        flash('This order is not ready for delivery confirmation.', 'warning')
        return redirect(url_for('order_details', order_id=order_id))
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'confirm':
            order.status = 'completed'
            order.actual_delivery = datetime.utcnow()
            
            # Add tracking update
            tracking = OrderTracking(
                order_id=order_id,
                status='delivered',
                notes='Buyer confirmed delivery'
            )
            db.session.add(tracking)
            
            db.session.commit()
            flash('✅ Thank you! Your delivery has been confirmed.', 'success')
            return redirect(url_for('order_details', order_id=order_id))
            
        elif action == 'issue':
            issue_description = request.form.get('issue_description', '').strip()
            
            if not issue_description:
                flash('Please describe the issue.', 'danger')
                return redirect(url_for('delivery_confirmation', order_id=order_id))
            
            try:
                # Determine issue type based on description keywords
                description_lower = issue_description.lower()
                if 'damaged' in description_lower or 'broken' in description_lower:
                    issue_type = 'damaged'
                elif 'wrong' in description_lower or 'different' in description_lower:
                    issue_type = 'wrong_item'
                elif 'missing' in description_lower or 'incomplete' in description_lower:
                    issue_type = 'missing'
                elif 'late' in description_lower or 'delay' in description_lower:
                    issue_type = 'late'
                elif 'seller' in description_lower or 'vendor' in description_lower:
                    issue_type = 'seller'
                else:
                    issue_type = 'other'
                
                # Set priority based on issue type
                if issue_type == 'damaged' or issue_type == 'wrong_item':
                    priority = 'high'
                elif issue_type == 'missing':
                    priority = 'urgent'
                else:
                    priority = 'medium'
                
                # Create the issue record
                issue = Issue(
                    order_id=order_id,
                    user_id=user.id,
                    issue_type=issue_type,
                    description=issue_description,
                    status='pending',
                    priority=priority,
                    created_at=datetime.utcnow()
                )
                db.session.add(issue)
                db.session.flush()
                
                # Add the initial message from customer
                customer_message = IssueMessage(
                    issue_id=issue.id,
                    sender_id=user.id,
                    message=issue_description,
                    is_admin_reply=False,
                    created_at=datetime.utcnow()
                )
                db.session.add(customer_message)
                
                # Update order status to indicate issue reported
                order.status = 'issue_reported'
                order.actual_delivery = None  # Not delivered if there's an issue
                
                # Add tracking update
                tracking = OrderTracking(
                    order_id=order_id,
                    status='issue_reported',
                    notes=f'Issue reported by buyer: {issue_description[:100]}'
                )
                db.session.add(tracking)
                
                db.session.commit()
                
                # Send notification to admin (optional - you can implement email notifications)
                admins = User.query.filter_by(user_type='admin').all()
                for admin in admins:
                    # You could send email notification here
                    pass
                
                flash('⚠️ Your issue has been reported. Our support team will contact you within 24-48 hours.', 'warning')
                return redirect(url_for('order_details', order_id=order_id))
                
            except Exception as e:
                db.session.rollback()
                print(f"Error reporting issue: {e}")
                traceback.print_exc()
                flash('Error reporting issue. Please try again or contact support.', 'danger')
                return redirect(url_for('delivery_confirmation', order_id=order_id))
    
    return render_template('delivery_confirmation.html', order=order)




@app.route('/api/order/return-action', methods=['POST'])
@login_required
def process_return_request():
    """API endpoint for sellers/admins to process return requests"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        user = get_current_user()
        
        order_id = data.get('order_id')
        action = data.get('action')  # 'approve' or 'reject'
        reason = data.get('reason', '')
        
        if not order_id or not action:
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400
        
        order = Order.query.get_or_404(order_id)
        
        # Check permissions (seller or admin only)
        if user.user_type not in ['seller', 'admin']:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        if user.user_type == 'seller':
            # Verify seller owns this order's items
            seller_items = OrderItem.query.filter_by(order_id=order_id, seller_id=user.id).first()
            if not seller_items:
                return jsonify({'success': False, 'message': 'You can only process returns for your own orders'}), 403
        
        # Find the issue related to this order
        issue = Issue.query.filter_by(order_id=order_id).order_by(Issue.created_at.desc()).first()
        
        if action == 'approve':
            order.status = 'return_approved'
            
            # Create return record or update issue
            if issue:
                issue.status = 'resolved'
                issue.action_taken = 'Return approved'
                issue.resolution_notes = reason
            
            message = 'Return request approved'
            
        elif action == 'reject':
            order.status = 'return_rejected'
            
            if issue:
                issue.status = 'closed'
                issue.action_taken = 'Return rejected'
                issue.resolution_notes = reason
            
            message = 'Return request rejected'
        else:
            return jsonify({'success': False, 'message': 'Invalid action'}), 400
        
        # Add tracking update
        tracking = OrderTracking(
            order_id=order_id,
            status=order.status,
            notes=f'Return {action}d. {reason}'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': message
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error processing return: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/order/reorder', methods=['POST'])
@login_required
def reorder_items():
    """API endpoint for buyers to reorder previous orders"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        order_id = data.get('order_id')
        
        if not order_id:
            return jsonify({'success': False, 'message': 'Order ID required'}), 400
        
        order = Order.query.get_or_404(order_id)
        
        # Verify user owns this order
        if order.user_id != user_id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        # Add items to cart
        items_added = 0
        for item in order.order_items:
            product = Product.query.get(item.product_id)
            
            if product and product.is_active and product.stock > 0:
                # Check if item already in cart
                existing_cart = Cart.query.filter_by(
                    user_id=user_id,
                    product_id=product.id
                ).first()
                
                if existing_cart:
                    # Update quantity (don't exceed stock)
                    new_qty = existing_cart.quantity + item.quantity
                    existing_cart.quantity = min(new_qty, product.stock)
                else:
                    # Add new cart item
                    cart_qty = min(item.quantity, product.stock)
                    cart_item = Cart(
                        user_id=user_id,
                        product_id=product.id,
                        quantity=cart_qty
                    )
                    db.session.add(cart_item)
                
                items_added += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'{items_added} items added to your cart',
            'cart_count': Cart.query.filter_by(user_id=user_id).count()
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error reordering: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/boda-rider/add', methods=['POST'])
@admin_required
def add_boda_rider_simple():
    """Simple endpoint to add a boda rider"""
    try:
        name = request.form.get('name')
        phone = request.form.get('phone')
        vehicle_type = request.form.get('vehicle_type', 'motorcycle')
        vehicle_number = request.form.get('vehicle_number')
        current_location = request.form.get('current_location')
        
        # Validate required fields
        if not name or not phone:
            flash('Name and phone are required!', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Check if phone already exists
        existing = DeliveryPerson.query.filter_by(phone=phone).first()
        if existing:
            flash('A rider with this phone number already exists!', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Create new rider
        rider = DeliveryPerson(
            name=name,
            phone=phone,
            vehicle_type=vehicle_type,
            vehicle_number=vehicle_number,
            current_location=current_location,
            is_active=True
        )
        
        db.session.add(rider)
        db.session.commit()
        
        flash(f'✅ Rider {name} added successfully!', 'success')
        return redirect(url_for('boda_riders'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding rider: {str(e)}', 'danger')
        return redirect(url_for('boda_riders'))



@app.route('/api/admin/delivery/live-positions')
@admin_required
def get_live_positions():
    """Get live positions for all active deliveries"""
    try:
        # Get all active assignments
        active_assignments = DeliveryAssignment.query.filter(
            DeliveryAssignment.status.in_(['assigned', 'picked_up', 'in_transit'])
        ).all()
        
        positions = []
        stats = {
            'completed_today': 0,
            'delayed_count': 0
        }
        
        # Calculate today's completed deliveries
        today = datetime.utcnow().replace(hour=0, minute=0, second=0)
        stats['completed_today'] = DeliveryAssignment.query.filter(
            DeliveryAssignment.status == 'delivered',
            DeliveryAssignment.completed_at >= today
        ).count()
        
        for assignment in active_assignments:
            order = assignment.order
            rider = assignment.delivery_person
            
            if rider and rider.current_location:
                # Try to parse lat/lng from location string (stored as "lat, lng")
                lat = None
                lng = None
                if rider.current_location and ',' in rider.current_location:
                    try:
                        parts = rider.current_location.split(',')
                        lat = float(parts[0].strip())
                        lng = float(parts[1].strip())
                    except:
                        pass
                
                # Get latest tracking point for battery/speed
                latest_tracking = DeliveryTracking.query.filter_by(
                    order_id=order.id
                ).order_by(DeliveryTracking.created_at.desc()).first()
                
                # Check if delivery is delayed
                if order.estimated_delivery and order.estimated_delivery < datetime.utcnow():
                    stats['delayed_count'] += 1
                
                positions.append({
                    'order_id': order.id,
                    'rider_name': rider.name,
                    'rider_phone': rider.phone,
                    'customer': order.user.fullname if order.user else 'N/A',
                    'delivery_address': order.delivery_address,
                    'lat': lat,
                    'lng': lng,
                    'status': assignment.status,
                    'battery': latest_tracking.battery_level if latest_tracking else None,
                    'speed': latest_tracking.speed if latest_tracking else 0,
                    'last_update': (latest_tracking.created_at.isoformat() if latest_tracking 
                                   else rider.updated_at.isoformat() if rider.updated_at 
                                   else datetime.utcnow().isoformat())
                })
        
        return jsonify({
            'success': True,
            'positions': positions,
            'stats': stats
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500






@app.route('/debug/nins-full')
def debug_nins_full():
    """Show all NINs with raw data"""
    try:
        nins = NINVerification.query.all()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Complete NIN Database</title>
            <style>
                body { font-family: monospace; padding: 20px; background: #1e293b; color: #e2e8f0; }
                .container { max-width: 1200px; margin: 0 auto; background: #0f172a; padding: 20px; border-radius: 12px; }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                th { background: #ff6b00; color: white; padding: 10px; text-align: left; }
                td { padding: 8px; border-bottom: 1px solid #334155; }
                .count { background: #ff6b00; padding: 5px 15px; border-radius: 20px; display: inline-block; }
                .nin-code { font-family: monospace; font-weight: bold; color: #ff6b00; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📋 Complete NIN Database</h1>
                <div class="count">Total: """ + str(len(nins)) + """ NINs</div>
                <table>
                    <thead>
                        <tr><th>ID</th><th>NIN Number</th><th>Full Name</th><th>Date of Birth</th><th>Created</th></tr>
                    </thead>
                    <tbody>
        """
        
        for nin in nins:
            html += f"""
                <tr>
                    <td>{nin.id}</td>
                    <td class="nin-code">{nin.nin}</td>
                    <td>{nin.full_name}</td>
                    <td>{nin.date_of_birth or 'N/A'}</td>
                    <td>{nin.created_at.strftime('%Y-%m-%d %H:%M') if nin.created_at else 'N/A'}</td>
                </tr>
            """
        
        html += """
                    </tbody>
                </table>
                <div style="margin-top: 20px;">
                    <a href="/reset-nin-database" style="background: #ff6b00; color: white; padding: 8px 16px; text-decoration: none; border-radius: 6px;">Reset to Default</a>
                    <a href="/add-more-nins" style="background: #10b981; color: white; padding: 8px 16px; text-decoration: none; border-radius: 6px; margin-left: 10px;">Add More NINs</a>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"









@app.route('/add-more-nins')
def add_more_nins():
    """Add ALL NINs to database"""
    try:
        # Complete list of all NINs
        all_nins = [
            # Original 8
            {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15"},
            {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22"},
            {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30"},
            {"nin": "CF321654987GH", "full_name": "Mary Williams", "dob": "1996-03-18"},
            {"nin": "CM789123456IJ", "full_name": "David Brown", "dob": "1994-07-25"},
            {"nin": "CF147258369KL", "full_name": "Sarah Taylor", "dob": "1997-09-12"},
            {"nin": "CM369258147MN", "full_name": "Michael Anderson", "dob": "1993-12-05"},
            {"nin": "CF951753456OP", "full_name": "Elizabeth Thomas", "dob": "1999-01-28"},
            
            # Additional NINs - Students
            {"nin": "CM159753248QR", "full_name": "James Wilson", "dob": "1995-04-10"},
            {"nin": "CF753951826ST", "full_name": "Patricia Moore", "dob": "1998-08-15"},
            {"nin": "CM852963741UV", "full_name": "Christopher Lee", "dob": "1994-12-20"},
            {"nin": "CF741852963WX", "full_name": "Linda Martinez", "dob": "1997-06-03"},
            {"nin": "CM963852741YZ", "full_name": "Daniel Rodriguez", "dob": "1996-09-18"},
            
            # More NINs
            {"nin": "CM111222333AB", "full_name": "Alice Nambi", "dob": "1999-03-12"},
            {"nin": "CF444555666CD", "full_name": "Brian Ssali", "dob": "1998-07-25"},
            {"nin": "CM777888999EF", "full_name": "Catherine Nalubega", "dob": "2000-01-05"},
            {"nin": "CF123987456GH", "full_name": "David Mukasa", "dob": "1997-11-14"},
            {"nin": "CM456789123IJ", "full_name": "Esther Nakato", "dob": "1999-08-30"},
            {"nin": "CF789123456KL", "full_name": "Frank Mutebi", "dob": "1996-04-22"},
            {"nin": "CM321654987MN", "full_name": "Grace Achieng", "dob": "2000-06-17"},
            {"nin": "CF654987321OP", "full_name": "Henry Okello", "dob": "1995-10-08"},
            {"nin": "CM987321654QR", "full_name": "Irene Namugenyi", "dob": "1998-12-03"},
            {"nin": "CF258147369ST", "full_name": "John Kato", "dob": "1994-02-28"},
            
            # UCU Student NINs
            {"nin": "CM555666777AB", "full_name": "Kevin Mukasa", "dob": "2001-03-15"},
            {"nin": "CF888999000CD", "full_name": "Linda Nansamba", "dob": "2000-07-22"},
            {"nin": "CM111222333EF", "full_name": "Moses Ssenyonga", "dob": "1999-11-30"},
            {"nin": "CF444555666GH", "full_name": "Nina Kyomugisha", "dob": "2001-01-18"},
            {"nin": "CM777888999IJ", "full_name": "Oscar Wasswa", "dob": "2000-05-05"},
            {"nin": "CF123456789KL", "full_name": "Phiona Nalule", "dob": "1999-09-09"},
            {"nin": "CM987654321MN", "full_name": "Ronald Ssekandi", "dob": "2000-12-12"},
            {"nin": "CF456789123OP", "full_name": "Sarah Nambi", "dob": "2001-04-04"},
            {"nin": "CM321789654QR", "full_name": "Thomas Mulindwa", "dob": "1998-08-08"},
            {"nin": "CF654321987ST", "full_name": "Umar Segawa", "dob": "2000-02-20"},
            
            # Faculty/Staff NINs
            {"nin": "CM147258369AB", "full_name": "Dr. Peter Okello", "dob": "1980-05-15"},
            {"nin": "CF258369147CD", "full_name": "Prof. Grace Nakato", "dob": "1975-08-22"},
            {"nin": "CM369147258EF", "full_name": "Dr. James Ssenyonjo", "dob": "1982-11-30"},
            {"nin": "CF147369258GH", "full_name": "Ms. Sarah Nabatanzi", "dob": "1988-03-18"},
            {"nin": "CM258147369IJ", "full_name": "Mr. David Kato", "dob": "1985-07-25"},
            {"nin": "CF369258147KL", "full_name": "Dr. Maria Namugenyi", "dob": "1979-09-12"},
        ]
        
        # Keep track of added vs skipped
        added_count = 0
        skipped_count = 0
        
        for nin_data in all_nins:
            # Check if NIN already exists
            existing = NINVerification.query.filter_by(nin=nin_data["nin"]).first()
            if not existing:
                nin = NINVerification(
                    nin=nin_data["nin"],
                    full_name=nin_data["full_name"],
                    date_of_birth=nin_data["dob"],
                    is_valid=True
                )
                db.session.add(nin)
                added_count += 1
                print(f"➕ Added: {nin_data['nin']} - {nin_data['full_name']}")
            else:
                skipped_count += 1
                print(f"⏭️ Skipped (exists): {nin_data['nin']}")
        
        db.session.commit()
        
        total_count = NINVerification.query.count()
        
        # Build HTML response
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>NIN Database Updated</title>
            <style>
                body {{ font-family: Arial; padding: 40px; background: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; border-radius: 16px; padding: 30px; }}
                .success {{ color: #10b981; font-size: 24px; }}
                .stats {{ background: #f8fafc; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .btn {{ display: inline-block; padding: 10px 20px; background: #ff6b00; color: white; text-decoration: none; border-radius: 6px; margin-right: 10px; }}
                .btn-secondary {{ background: #000080; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1 class="success">✅ NIN Database Updated!</h1>
                <div class="stats">
                    <p><strong>Added:</strong> {added_count} new NINs</p>
                    <p><strong>Skipped:</strong> {skipped_count} existing NINs</p>
                    <p><strong>Total NINs now:</strong> {total_count}</p>
                </div>
                
                <h3>Sample NINs for Testing:</h3>
                <ul>
                    <li><code>CM123456789AB</code> - John Doe</li>
                    <li><code>CF987654321CD</code> - Jane Smith</li>
                    <li><code>CM111222333AB</code> - Alice Nambi</li>
                    <li><code>CM555666777AB</code> - Kevin Mukasa</li>
                    <li><code>CM147258369AB</code> - Dr. Peter Okello</li>
                </ul>
                
                <div style="margin-top: 20px;">
                    <a href="/debug/nins-full" class="btn">View All NINs</a>
                    <a href="/" class="btn btn-secondary">Go to Home</a>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        db.session.rollback()
        return f"<h1>Error: {str(e)}</h1><pre>{traceback.format_exc()}</pre>"        









@app.route('/reset-nin-database')
def reset_nin_database():
    """Reset and populate NIN database with all NINs"""
    try:
        # Clear existing NINs
        NINVerification.query.delete()
        
        # All NINs (same list as above)
        all_nins = [
            # ... (paste all the NINs from above)
        ]
        
        added_count = 0
        for nin_data in all_nins:
            nin = NINVerification(
                nin=nin_data["nin"],
                full_name=nin_data["full_name"],
                date_of_birth=nin_data["dob"],
                is_valid=True
            )
            db.session.add(nin)
            added_count += 1
        
        db.session.commit()
        
        return f"""
        <html>
        <head><title>NIN Database Reset</title></head>
        <body style="font-family: Arial; padding: 40px;">
            <h1 style="color: green;">✅ NIN Database Reset Successfully!</h1>
            <p>Added <strong>{added_count}</strong> demo NINs to the database.</p>
            <p><a href="/debug/nins-full">View All NINs</a> | <a href="/">Go to Home</a></p>
        </body>
        </html>
        """
    except Exception as e:
        db.session.rollback()
        return f"<h1>Error: {str(e)}</h1>"





@app.route('/dashboard')
@login_required
def dashboard():
    """Seller dashboard with real analytics"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Dashboard is only available for sellers.', 'info')
        return redirect(url_for('products'))
    
    try:
        # Get all products for this seller
        all_products = Product.query.filter_by(seller_id=user.id).all()
        
        # Calculate product stats
        total_products = len(all_products)
        active_products = sum(1 for p in all_products if p.is_active)
        inactive_count = total_products - active_products
        
        # Calculate inventory value
        inventory_value = sum(p.price * p.stock for p in all_products if p.is_active)
        
        # Calculate total sales (completed orders only)
        total_sales_result = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed'
        ).scalar()
        total_sales = float(total_sales_result) if total_sales_result else 0
        
        # Count unique customers
        unique_customers = db.session.query(
            func.count(db.distinct(Order.user_id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed'
        ).scalar() or 0
        
        # Calculate store rating
        reviews = db.session.query(Review).join(Product).filter(
            Product.seller_id == user.id
        ).all()
        
        if reviews:
            avg_rating = sum(r.rating for r in reviews) / len(reviews)
            reviews_count = len(reviews)
        else:
            avg_rating = 0
            reviews_count = 0
        
        # Calculate period-over-period changes
        today = datetime.utcnow()
        current_period_start = today - timedelta(days=30)
        previous_period_start = today - timedelta(days=60)
        
        # Products change
        current_products = Product.query.filter(
            Product.seller_id == user.id,
            Product.created_at >= current_period_start
        ).count()
        previous_products = Product.query.filter(
            Product.seller_id == user.id,
            Product.created_at >= previous_period_start,
            Product.created_at < current_period_start
        ).count()
        products_change = calculate_percentage_change(current_products, previous_products)
        
        # Sales change
        current_sales = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= current_period_start
        ).scalar() or 0
        
        previous_sales = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= previous_period_start,
            Order.created_at < current_period_start
        ).scalar() or 0
        
        sales_change = calculate_percentage_change(current_sales, previous_sales)
        
        # Customers change
        current_customers = db.session.query(
            func.count(db.distinct(Order.user_id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= current_period_start
        ).scalar() or 0
        
        previous_customers = db.session.query(
            func.count(db.distinct(Order.user_id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= previous_period_start,
            Order.created_at < current_period_start
        ).scalar() or 0
        
        customers_change = calculate_percentage_change(current_customers, previous_customers)
        
        store_stats = {
            'products': active_products,
            'total_sales': total_sales,
            'customers': unique_customers,
            'rating': round(avg_rating, 1),
            'products_change': products_change,
            'sales_change': sales_change,
            'customers_change': customers_change,
            'total_products': total_products,
            'inactive_count': inactive_count,
            'inventory_value': inventory_value
        }
        
        # Quick stats for today
        today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Today's revenue
        today_revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= today_start
        ).scalar() or 0
        
        # Pending orders (orders that need attention)
        pending_orders = db.session.query(
            func.count(db.distinct(Order.id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status.in_(['pending', 'confirmed', 'processing'])
        ).scalar() or 0
        
        # Products sold today
        products_sold_today = db.session.query(
            func.sum(OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= today_start
        ).scalar() or 0
        
        # Low stock count
        low_stock_count = Product.query.filter(
            Product.seller_id == user.id,
            Product.stock <= 5,
            Product.stock > 0,
            Product.is_active == True
        ).count()
        
        quick_stats = {
            'today_revenue': today_revenue,
            'pending_orders': pending_orders,
            'rating': round(avg_rating, 1),
            'products_sold': products_sold_today,
            'low_stock_count': low_stock_count
        }
        
        # Recent orders (last 5)
        recent_orders = db.session.query(Order).distinct().join(OrderItem).filter(
            OrderItem.seller_id == user.id
        ).order_by(Order.created_at.desc()).limit(5).all()
        
        # Low stock products
        low_stock_products = Product.query.filter(
            Product.seller_id == user.id,
            Product.stock <= 5,
            Product.stock > 0,
            Product.is_active == True
        ).order_by(Product.stock.asc()).limit(5).all()
        
        # Recent reviews
        recent_reviews = db.session.query(Review).join(Product).filter(
            Product.seller_id == user.id
        ).order_by(Review.created_at.desc()).limit(5).all()
        
        # Prepare chart data for last 7 days
        seven_days_ago = today - timedelta(days=7)
        
        # Get daily sales for last 7 days
        daily_sales = []
        for i in range(6, -1, -1):
            date = today - timedelta(days=i)
            day_start = datetime.combine(date.date(), datetime.min.time())
            day_end = datetime.combine(date.date(), datetime.max.time())
            
            revenue = db.session.query(
                func.sum(OrderItem.price * OrderItem.quantity)
            ).join(Order).filter(
                OrderItem.seller_id == user.id,
                Order.status == 'completed',
                Order.created_at >= day_start,
                Order.created_at <= day_end
            ).scalar() or 0
            
            daily_sales.append(float(revenue))
        
        chart_labels = [(today - timedelta(days=i)).strftime('%a') for i in range(6, -1, -1)]
        chart_data = daily_sales
        
        max_chart_value = max(chart_data) if chart_data else 0
        
        # Calculate conversion rate (if you have view tracking)
        conversion_rate = 0  # Implement if you have view tracking
        
        # Current month revenue
        month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        current_month_revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= month_start
        ).scalar() or 0
        
        analytics_data = {
            'reviews_count': reviews_count,
            'line_chart_labels': chart_labels,
            'line_chart_data': chart_data,
            'max_chart_value': max_chart_value,
            'conversion_rate': conversion_rate,
            'current_month_revenue': current_month_revenue
        }
        
        print(f"✅ Dashboard loaded - Products: {active_products}, Revenue: {total_sales}, Customers: {unique_customers}")
        
    except Exception as e:
        print(f"❌ Error loading dashboard data: {e}")
        traceback.print_exc()
        
        # Default values in case of error
        store_stats = {
            'products': 0,
            'total_sales': 0,
            'customers': 0,
            'rating': 0,
            'products_change': 0,
            'sales_change': 0,
            'customers_change': 0,
            'total_products': 0,
            'inactive_count': 0,
            'inventory_value': 0
        }
        quick_stats = {
            'today_revenue': 0,
            'pending_orders': 0,
            'rating': 0,
            'products_sold': 0,
            'low_stock_count': 0
        }
        recent_orders = []
        low_stock_products = []
        recent_reviews = []
        
        chart_labels = [(datetime.utcnow() - timedelta(days=i)).strftime('%a') for i in range(6, -1, -1)]
        
        analytics_data = {
            'reviews_count': 0,
            'line_chart_labels': chart_labels,
            'line_chart_data': [0, 0, 0, 0, 0, 0, 0],
            'max_chart_value': 0,
            'conversion_rate': 0,
            'current_month_revenue': 0
        }
    
    return render_template('seller_dashboard.html',
        user=user,
        store_stats=store_stats,
        recent_orders=recent_orders,
        low_stock_products=low_stock_products,
        quick_stats=quick_stats,
        recent_reviews=recent_reviews,
        analytics_data=analytics_data
    )



@app.route('/debug-routes')
def debug_routes():
    """List all registered routes"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'url': str(rule)
        })
    
    html = "<html><head><title>Debug Routes</title></head><body>"
    html += "<h1>📋 Registered Routes</h1>"
    html += "<table border='1' cellpadding='5'>"
    html += "<tr><th>Endpoint</th><th>URL</th><th>Methods</th></tr>"
    
    for route in sorted(routes, key=lambda x: x['url']):
        html += f"<tr>"
        html += f"<td>{route['endpoint']}</td>"
        html += f"<td>{route['url']}</td>"
        html += f"<td>{', '.join(route['methods'])}</td>"
        html += f"</tr>"
    
    html += "</table>"
    html += "</body></html>"
    return html


@app.route('/test')
def test():
    return "✅ Flask is working!"


@app.route('/create-test-issue-with-messages')
@admin_required
def create_test_issue_with_messages():
    """Create test issues with complete message threads"""
    try:
        # Get some orders
        orders = Order.query.limit(3).all()
        if not orders:
            return "No orders found. Please create some orders first."
        
        # Get admin user
        admin = User.query.filter_by(user_type='admin').first()
        if not admin:
            return "No admin user found."
        
        count = 0
        
        for i, order in enumerate(orders):
            # Check if issue already exists for this order
            existing = Issue.query.filter_by(order_id=order.id).first()
            if existing:
                continue
                
            # Create issue
            issue_types = ['damaged', 'wrong_item', 'missing', 'late', 'other']
            priorities = ['low', 'medium', 'high', 'urgent']
            
            issue = Issue(
                order_id=order.id,
                user_id=order.user_id,
                issue_type=issue_types[i % len(issue_types)],
                description=f"Test issue #{i+1}: Customer reports problem with order #{order.id}",
                status='pending',
                priority=priorities[i % len(priorities)],
                created_at=datetime.utcnow()
            )
            db.session.add(issue)
            db.session.flush()
            
            # Add customer message
            customer_msg = IssueMessage(
                issue_id=issue.id,
                sender_id=order.user_id,
                message=f"I received my order #{order.id} but there's a problem. Please help!",
                is_admin_reply=False,
                created_at=datetime.utcnow()
            )
            db.session.add(customer_msg)
            
            # Add admin response (for some issues)
            if i % 2 == 0:  # Every other issue has admin response
                admin_msg = IssueMessage(
                    issue_id=issue.id,
                    sender_id=admin.id,
                    message=f"We're sorry to hear that. Our team is looking into this issue for order #{order.id}.",
                    is_admin_reply=True,
                    created_at=datetime.utcnow() + timedelta(minutes=5)
                )
                db.session.add(admin_msg)
                
                # Update issue status
                issue.status = 'in_progress'
                issue.responded_by = admin.id
                issue.responded_at = datetime.utcnow() + timedelta(minutes=5)
            
            count += 1
        
        db.session.commit()
        
        return f"""
        <html>
        <head>
            <title>Test Issues Created</title>
            <style>
                body {{ font-family: Arial; padding: 40px; }}
                .success {{ color: green; }}
                a {{ background: #ff6b00; color: white; padding: 10px 20px; 
                     text-decoration: none; border-radius: 5px; display: inline-block; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <h1 class="success">✅ Test Issues Created Successfully!</h1>
            <p>Created {count} test issues with complete message threads.</p>
            <p>Each issue has customer messages and some have admin replies.</p>
            <a href="/admin/issues">View Issues Now</a>
        </body>
        </html>
        """
        
    except Exception as e:
        db.session.rollback()
        return f"Error: {str(e)}"





@app.route('/debug-issue-messages/<int:issue_id>')
@admin_required
def debug_issue_messages(issue_id):
    """Debug route to check messages for an issue"""
    issue = Issue.query.get_or_404(issue_id)
    messages = IssueMessage.query.filter_by(issue_id=issue_id).order_by(IssueMessage.created_at).all()
    
    html = f"<h1>Messages for Issue #{issue_id}</h1>"
    html += f"<p>Issue: {issue.description}</p>"
    html += f"<p>Total messages: {len(messages)}</p>"
    
    if messages:
        html += "<table border='1' cellpadding='5'>"
        html += "<tr><th>ID</th><th>Sender</th><th>Message</th><th>Admin Reply</th><th>Created</th></tr>"
        for msg in messages:
            html += f"<tr>"
            html += f"<td>{msg.id}</td>"
            html += f"<td>{msg.sender.fullname if msg.sender else 'Unknown'}</td>"
            html += f"<td>{msg.message}</td>"
            html += f"<td>{'✅' if msg.is_admin_reply else '❌'}</td>"
            html += f"<td>{msg.created_at}</td>"
            html += f"</tr>"
        html += "</table>"
    else:
        html += "<p style='color:red'>❌ No messages found for this issue!</p>"
    
    html += f'<p><a href="/admin/issues">Back to Issues</a></p>'
    return html




@app.route('/add-test-message/<int:issue_id>')
@admin_required
def add_test_message(issue_id):
    """Add a test message to an issue"""
    issue = Issue.query.get_or_404(issue_id)
    
    message = IssueMessage(
        issue_id=issue_id,
        sender_id=issue.user_id,
        message="This is a test message from the buyer. The product arrived damaged and I need help.",
        is_admin_reply=False,
        created_at=datetime.utcnow()
    )
    db.session.add(message)
    db.session.commit()
    
    return f"✅ Test message added to Issue #{issue_id}"



@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    """Admin analytics with real data"""
    user = get_current_user()
    
    # Get period from request
    period = request.args.get('period', 'month')
    
    # Calculate date ranges
    end_date = datetime.utcnow()
    if period == 'today':
        start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = end_date - timedelta(days=7)
    elif period == 'month':
        start_date = end_date - timedelta(days=30)
    elif period == 'quarter':
        start_date = end_date - timedelta(days=90)
    elif period == 'year':
        start_date = end_date - timedelta(days=365)
    else:
        start_date = end_date - timedelta(days=30)
    
    # Calculate previous period for comparison
    period_days = (end_date - start_date).days
    prev_start = start_date - timedelta(days=period_days)
    prev_end = start_date - timedelta(days=1)
    
    try:
        # Current period revenue
        current_revenue = db.session.query(
            func.sum(Order.total_amount)
        ).filter(
            Order.status == 'completed',
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).scalar() or 0
        
        # Previous period revenue
        prev_revenue = db.session.query(
            func.sum(Order.total_amount)
        ).filter(
            Order.status == 'completed',
            Order.created_at >= prev_start,
            Order.created_at <= prev_end
        ).scalar() or 0
        
        revenue_change = ((current_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
        
        # Current period orders
        current_orders = Order.query.filter(
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).count()
        
        prev_orders = Order.query.filter(
            Order.created_at >= prev_start,
            Order.created_at <= prev_end
        ).count()
        
        orders_change = ((current_orders - prev_orders) / prev_orders * 100) if prev_orders > 0 else 0
        
        # Products sold
        products_sold = db.session.query(
            func.sum(OrderItem.quantity)
        ).join(Order).filter(
            Order.status == 'completed',
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).scalar() or 0
        
        # Unique customers
        unique_customers = db.session.query(
            func.count(db.distinct(Order.user_id))
        ).filter(
            Order.status == 'completed',
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).scalar() or 0
        
        # Store rating (overall)
        all_reviews = Review.query.all()
        store_rating = sum(r.rating for r in all_reviews) / len(all_reviews) if all_reviews else 0
        
        # Active products
        active_products = Product.query.filter_by(is_active=True).count()
        
        # Category sales
        category_sales = db.session.query(
            Product.category,
            func.sum(OrderItem.quantity).label('quantity'),
            func.sum(OrderItem.price * OrderItem.quantity).label('revenue')
        ).join(OrderItem, Product.id == OrderItem.product_id
        ).join(Order, OrderItem.order_id == Order.id
        ).filter(
            Order.status == 'completed',
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).group_by(Product.category).all()
        
        category_data = []
        for cat, qty, rev in category_sales:
            if cat:
                category_data.append({
                    'category': cat.replace('_', ' ').title(),
                    'quantity': qty or 0,
                    'revenue': float(rev or 0)
                })
        
        # Top products
        top_products = db.session.query(
            Product.id,
            Product.name,
            Product.price,
            func.sum(OrderItem.quantity).label('total_sold'),
            func.sum(OrderItem.price * OrderItem.quantity).label('revenue'),
            func.avg(Review.rating).label('avg_rating')
        ).outerjoin(OrderItem, Product.id == OrderItem.product_id
        ).outerjoin(Order, OrderItem.order_id == Order.id
        ).outerjoin(Review, Review.product_id == Product.id
        ).filter(
            Order.status == 'completed',
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).group_by(Product.id, Product.name, Product.price
        ).order_by(func.sum(OrderItem.quantity).desc()
        ).limit(5).all()
        
        top_products_list = []
        for prod in top_products:
            top_products_list.append({
                'id': prod.id,
                'name': prod.name,
                'price': float(prod.price),
                'total_sold': prod.total_sold or 0,
                'revenue': float(prod.revenue or 0),
                'avg_rating': float(prod.avg_rating or 0)
            })
        
        # Recent orders
        recent_orders = Order.query.options(
            db.joinedload(Order.user),
            db.joinedload(Order.order_items)
        ).filter(
            Order.created_at >= start_date,
            Order.created_at <= end_date
        ).order_by(Order.created_at.desc()).limit(5).all()
        
        # Chart data (last 30 days)
        chart_labels = []
        chart_data = []
        for i in range(29, -1, -1):
            date = end_date - timedelta(days=i)
            chart_labels.append(date.strftime('%d %b'))
            
            day_revenue = db.session.query(
                func.sum(Order.total_amount)
            ).filter(
                Order.status == 'completed',
                Order.created_at >= date.replace(hour=0, minute=0, second=0),
                Order.created_at <= date.replace(hour=23, minute=59, second=59)
            ).scalar() or 0
            chart_data.append(float(day_revenue))
        
        analytics_data = {
            'total_revenue': float(current_revenue),
            'revenue_change': float(revenue_change),
            'total_orders': current_orders,
            'orders_change': float(orders_change),
            'products_sold': products_sold,
            'store_rating': round(store_rating, 1),
            'reviews_count': len(all_reviews),
            'store_views': 0,  # Implement if you have view tracking
            'unique_customers': unique_customers,
            'active_products': active_products,
            'conversion_rate': 0,  # Implement if you have view tracking
            'line_chart_labels': chart_labels,
            'line_chart_data': chart_data,
            'category_sales': category_data,
            'top_products': top_products_list,
            'recent_orders': recent_orders
        }
        
    except Exception as e:
        print(f"Error in analytics: {e}")
        traceback.print_exc()
        
        # Default empty data
        analytics_data = {
            'total_revenue': 0,
            'revenue_change': 0,
            'total_orders': 0,
            'orders_change': 0,
            'products_sold': 0,
            'store_rating': 0,
            'reviews_count': 0,
            'store_views': 0,
            'unique_customers': 0,
            'active_products': 0,
            'conversion_rate': 0,
            'line_chart_labels': [(end_date - timedelta(days=i)).strftime('%d %b') for i in range(29, -1, -1)],
            'line_chart_data': [0] * 30,
            'category_sales': [],
            'top_products': [],
            'recent_orders': []
        }
    
    return render_template('admin_analytics.html',
                         user=user,
                         analytics_data=analytics_data,
                         period=period,
                         start_date=start_date.strftime('%d %b %Y'),
                         end_date=end_date.strftime('%d %b %Y'))



@app.route('/seller/products')
@login_required
def manage_products():
    """Manage products page with real analytics"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied. Seller account required.', 'danger')
        return redirect(url_for('products'))
    
    # Get filter parameters
    filter_status = request.args.get('filter', 'all')
    sort_by = request.args.get('sort', 'newest')
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    # Base query
    query = Product.query.filter_by(seller_id=user.id)
    
    # Apply search
    if search_query:
        query = query.filter(
            db.or_(
                Product.name.ilike(f'%{search_query}%'),
                Product.description.ilike(f'%{search_query}%'),
                Product.category.ilike(f'%{search_query}%')
            )
        )
    
    # Apply status filter
    if filter_status == 'active':
        query = query.filter_by(is_active=True)
    elif filter_status == 'inactive':
        query = query.filter_by(is_active=False)
    elif filter_status == 'low-stock':
        query = query.filter(Product.stock <= 5, Product.stock > 0)
    
    # Apply sorting
    if sort_by == 'newest':
        query = query.order_by(Product.created_at.desc())
    elif sort_by == 'oldest':
        query = query.order_by(Product.created_at.asc())
    elif sort_by == 'price-high':
        query = query.order_by(Product.price.desc())
    elif sort_by == 'price-low':
        query = query.order_by(Product.price.asc())
    elif sort_by == 'name':
        query = query.order_by(Product.name.asc())
    elif sort_by == 'stock':
        query = query.order_by(Product.stock.asc())
    elif sort_by == 'most-sold':
        query = query.order_by(Product.sold_count.desc())
    
    # Get paginated results
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    products = pagination.items
    
    # Calculate analytics for each product
    for product in products:
        # Get average rating
        reviews = Review.query.filter_by(product_id=product.id).all()
        product.avg_rating = sum(r.rating for r in reviews) / len(reviews) if reviews else 0
        product.review_count = len(reviews)
        
        # Get view count (if you have a ProductView model)
        product.view_count = 0  # Placeholder - implement if you have view tracking
    
    # Calculate overall stats
    all_products = Product.query.filter_by(seller_id=user.id).all()
    total_products = len(all_products)
    active_products = sum(1 for p in all_products if p.is_active)
    low_stock_count = sum(1 for p in all_products if p.stock <= 5 and p.stock > 0)
    inventory_value = sum(p.price * p.stock for p in all_products)
    
    # Calculate total sold
    total_sold = sum(p.sold_count or 0 for p in all_products)
    
    # Calculate average rating across all products
    all_reviews = Review.query.join(Product).filter(Product.seller_id == user.id).all()
    avg_rating = sum(r.rating for r in all_reviews) / len(all_reviews) if all_reviews else 0
    total_reviews = len(all_reviews)
    
    # Calculate total views (placeholder)
    total_views = 0
    
    # Calculate sales change (this month vs last month)
    today = datetime.utcnow()
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    
    this_month_sales = db.session.query(
        func.sum(OrderItem.quantity)
    ).join(Order).filter(
        OrderItem.seller_id == user.id,
        Order.status == 'completed',
        Order.created_at >= month_start
    ).scalar() or 0
    
    last_month_sales = db.session.query(
        func.sum(OrderItem.quantity)
    ).join(Order).filter(
        OrderItem.seller_id == user.id,
        Order.status == 'completed',
        Order.created_at >= last_month_start,
        Order.created_at < month_start
    ).scalar() or 0
    
    sales_change = ((this_month_sales - last_month_sales) / last_month_sales * 100) if last_month_sales > 0 else 0
    
    # Calculate product revenue
    product_revenue = db.session.query(
        func.sum(OrderItem.price * OrderItem.quantity)
    ).join(Order).filter(
        OrderItem.seller_id == user.id,
        Order.status == 'completed'
    ).scalar() or 0
    
    print(f"✅ Manage Products - Total: {total_products}, Active: {active_products}, Revenue: {product_revenue}")
    
    return render_template('manage_products.html',
        user=user,
        products=products,
        pagination=pagination,
        total_products=total_products,
        active_products=active_products,
        low_stock_count=low_stock_count,
        inventory_value=inventory_value,
        total_views=total_views,
        avg_rating=avg_rating,
        total_reviews=total_reviews,
        total_sold=total_sold,
        sales_change=sales_change,
        product_revenue=product_revenue,
        filter_status=filter_status,
        sort_by=sort_by,
        search_query=search_query
    )




# ==================== DELIVERY PERSON MANAGEMENT ROUTES ====================

@app.route('/admin/delivery/person/add', methods=['POST'])
@admin_required
def add_delivery_person():
    """Add a new delivery person"""
    try:
        # Get form data
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        vehicle_type = request.form.get('vehicle_type', 'motorcycle')
        vehicle_number = request.form.get('vehicle_number', '').strip()
        current_location = request.form.get('current_location', '').strip()
        
        print(f"Adding rider: {name}, {phone}, {vehicle_type}")  # Debug log
        
        # Validate required fields
        if not name:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Name is required'}), 400
            flash('Name is required', 'danger')
            return redirect(url_for('boda_riders'))
        
        if not phone:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Phone number is required'}), 400
            flash('Phone number is required', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Validate phone format (10 digits)
        if not re.match(r'^[0-9]{10}$', phone):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Phone number must be 10 digits'}), 400
            flash('Phone number must be 10 digits', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Check if phone already exists
        existing = DeliveryPerson.query.filter_by(phone=phone).first()
        if existing:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'A rider with this phone number already exists'}), 400
            flash('A rider with this phone number already exists', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Create new delivery person
        delivery_person = DeliveryPerson(
            name=name,
            phone=phone,
            vehicle_type=vehicle_type,
            vehicle_number=vehicle_number,
            current_location=current_location,
            is_active=True
        )
        
        db.session.add(delivery_person)
        db.session.commit()
        
        print(f"Rider added successfully with ID: {delivery_person.id}")  # Debug log
        
        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True, 
                'message': 'Rider added successfully!',
                'rider': {
                    'id': delivery_person.id,
                    'name': delivery_person.name,
                    'phone': delivery_person.phone
                }
            })
        
        flash('Delivery person added successfully!', 'success')
        return redirect(url_for('boda_riders'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error adding delivery person: {str(e)}")
        traceback.print_exc()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': str(e)}), 500
        
        flash(f'Error adding delivery person: {str(e)}', 'danger')
        return redirect(url_for('boda_riders'))


@app.route('/admin/delivery/person/<int:person_id>/edit', methods=['POST'])
@admin_required
def edit_delivery_person(person_id):
    """Edit delivery person details"""
    try:
        person = DeliveryPerson.query.get_or_404(person_id)
        
        # Get form data
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        vehicle_type = request.form.get('vehicle_type', 'motorcycle')
        vehicle_number = request.form.get('vehicle_number', '').strip()
        current_location = request.form.get('current_location', '').strip()
        is_active = request.form.get('is_active') == 'true'
        
        print(f"Editing rider {person_id}: {name}, {phone}, active: {is_active}")  # Debug log
        
        # Validate required fields
        if not name:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Name is required'}), 400
            flash('Name is required', 'danger')
            return redirect(url_for('boda_riders'))
        
        if not phone:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Phone number is required'}), 400
            flash('Phone number is required', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Validate phone format
        if not re.match(r'^[0-9]{10}$', phone):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Phone number must be 10 digits'}), 400
            flash('Phone number must be 10 digits', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Check if phone already exists for another rider
        existing = DeliveryPerson.query.filter(DeliveryPerson.phone == phone, DeliveryPerson.id != person_id).first()
        if existing:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': 'Phone number already in use by another rider'}), 400
            flash('Phone number already in use by another rider', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Update fields
        person.name = name
        person.phone = phone
        person.vehicle_type = vehicle_type
        person.vehicle_number = vehicle_number
        person.current_location = current_location
        person.is_active = is_active
        
        db.session.commit()
        
        print(f"Rider {person_id} updated successfully")  # Debug log
        
        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True, 
                'message': 'Rider updated successfully!',
                'rider': {
                    'id': person.id,
                    'name': person.name,
                    'phone': person.phone
                }
            })
        
        flash('Rider updated successfully!', 'success')
        return redirect(url_for('boda_riders'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating rider: {str(e)}")
        traceback.print_exc()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': str(e)}), 500
        
        flash(f'Error updating rider: {str(e)}', 'danger')
        return redirect(url_for('boda_riders'))


@app.route('/admin/delivery/person/<int:person_id>/toggle', methods=['POST'])
@admin_required
def toggle_delivery_person_status(person_id):
    """Toggle delivery person active status"""
    try:
        person = DeliveryPerson.query.get_or_404(person_id)
        person.is_active = not person.is_active
        db.session.commit()
        
        status = "activated" if person.is_active else "deactivated"
        
        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True, 
                'message': f'Rider {status} successfully!',
                'is_active': person.is_active
            })
        
        flash(f'Rider {status} successfully!', 'success')
        return redirect(url_for('boda_riders'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error toggling rider status: {str(e)}")
        traceback.print_exc()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': str(e)}), 500
        
        flash(f'Error updating rider status: {str(e)}', 'danger')
        return redirect(url_for('boda_riders'))


@app.route('/admin/delivery/person/<int:person_id>/delete', methods=['POST'])
@admin_required
def delete_delivery_person(person_id):
    """Delete a delivery person permanently"""
    try:
        person = DeliveryPerson.query.get_or_404(person_id)
        
        # Check if rider has active deliveries
        active_assignments = DeliveryAssignment.query.filter(
            DeliveryAssignment.delivery_person_id == person_id,
            DeliveryAssignment.status.in_(['assigned', 'picked_up', 'in_transit'])
        ).first()
        
        if active_assignments:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({
                    'success': False, 
                    'message': 'Cannot delete rider with active deliveries. Deactivate them instead.'
                }), 400
            flash('Cannot delete rider with active deliveries. Deactivate them instead.', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Store name for message
        name = person.name
        
        # Delete the rider
        db.session.delete(person)
        db.session.commit()
        
        print(f"Rider {name} deleted successfully")  # Debug log
        
        # Check if it's an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True, 
                'message': f'Rider {name} deleted successfully!'
            })
        
        flash(f'Rider {name} deleted successfully!', 'success')
        return redirect(url_for('boda_riders'))
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting rider: {str(e)}")
        traceback.print_exc()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': str(e)}), 500
        
        flash(f'Error deleting rider: {str(e)}', 'danger')
        return redirect(url_for('boda_riders'))


@app.route('/api/admin/delivery/persons/<int:person_id>')
@admin_required
def get_delivery_person_details(person_id):
    """Get delivery person details with assignments"""
    try:
        person = DeliveryPerson.query.get_or_404(person_id)
        
        # Get all assignments (orders) for this rider
        assignments = []
        for order in person.deliveries:
            assignments.append({
                'id': order.id,
                'order_id': order.id,
                'status': order.status,
                'customer_name': order.user.fullname if order.user else 'N/A',
                'customer_phone': order.user.phone if order.user else 'N/A',
                'assigned_at': order.created_at.isoformat() if order.created_at else None,
                'completed_at': order.actual_delivery.isoformat() if order.actual_delivery else None
            })
        
        data = {
            'id': person.id,
            'name': person.name,
            'phone': person.phone,
            'vehicle_type': person.vehicle_type,
            'vehicle_number': person.vehicle_number,
            'current_location': person.current_location,
            'is_active': person.is_active,
            'created_at': person.created_at.isoformat() if hasattr(person, 'created_at') and person.created_at else None,
            'assignments': assignments
        }
        
        return jsonify(data)
        
    except Exception as e:
        print(f"Error fetching rider details: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/delivery/persons/available')
@admin_required
def get_available_riders():
    """Get all active delivery persons for assignment"""
    try:
        persons = DeliveryPerson.query.filter_by(is_active=True).all()
        
        riders_data = []
        for person in persons:
            # Count active deliveries
            active_count = DeliveryAssignment.query.filter_by(
                delivery_person_id=person.id
            ).filter(
                DeliveryAssignment.status.in_(['assigned', 'picked_up', 'in_transit'])
            ).count()
            
            riders_data.append({
                'id': person.id,
                'name': person.name,
                'phone': person.phone,
                'vehicle_type': person.vehicle_type,
                'vehicle_number': person.vehicle_number,
                'active_deliveries': active_count
            })
        
        return jsonify({'riders': riders_data, 'success': True})
    except Exception as e:
        print(f"Error loading riders: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/admin/delivery/tracking')
@admin_required
def delivery_tracking():
    """Unified delivery tracking page"""
    user = get_current_user()
    
    # Get active assignments (in_progress deliveries)
    active_assignments = DeliveryAssignment.query.filter(
        DeliveryAssignment.status.in_(['assigned', 'picked_up', 'in_transit'])
    ).order_by(DeliveryAssignment.assigned_at.desc()).all()
    
    # Count statistics
    pending_deliveries_count = DeliveryAssignment.query.filter_by(status='assigned').count()
    in_transit_count = DeliveryAssignment.query.filter(
        DeliveryAssignment.status.in_(['picked_up', 'in_transit'])
    ).count()
    
    today = datetime.utcnow().replace(hour=0, minute=0, second=0)
    completed_today_count = DeliveryAssignment.query.filter(
        DeliveryAssignment.status == 'delivered',
        DeliveryAssignment.completed_at >= today
    ).count()
    
    total_active = pending_deliveries_count + in_transit_count
    
    recent_tracking_updates = OrderTracking.query.order_by(
        OrderTracking.created_at.desc()
    ).limit(20).all()
    
    # Get all delivery persons for assignment dropdown
    delivery_persons = DeliveryPerson.query.filter_by(is_active=True).all()
    
    # IMPORTANT: Do NOT pass 'now' to the template
    return render_template('delivery_tracking.html',
                         user=user,
                         active_assignments=active_assignments,
                         pending_deliveries_count=pending_deliveries_count,
                         in_transit_count=in_transit_count,
                         completed_today_count=completed_today_count,
                         total_active=total_active,
                         recent_tracking_updates=recent_tracking_updates,
                         delivery_persons=delivery_persons)





@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard homepage"""
    user = get_current_user()
    
    # Get statistics
    total_users = User.query.count()
    total_products = Product.query.count()
    total_orders = Order.query.count()
    total_revenue = db.session.query(db.func.sum(Order.total_amount)).filter_by(status='completed').scalar() or 0
    
    # Get counts by type
    total_sellers = User.query.filter_by(user_type='seller').count()
    total_buyers = User.query.filter_by(user_type='buyer').count()
    total_admins = User.query.filter_by(user_type='admin').count()
    
    # Get recent data
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    
    # Get counts for the last week
    week_ago = datetime.utcnow() - timedelta(days=7)
    new_users_week = User.query.filter(User.created_at >= week_ago).count()
    new_sellers_week = User.query.filter(User.created_at >= week_ago, User.user_type == 'seller').count()
    
    # Get active products
    active_products = Product.query.filter_by(is_active=True).count()
    
    # Get order status counts
    pending_orders = Order.query.filter_by(status='pending').count()
    confirmed_orders = Order.query.filter_by(status='confirmed').count()
    processing_orders = Order.query.filter_by(status='processing').count()
    
    # OUT FOR DELIVERY - Count all orders that are on the way
    out_for_delivery_orders = Order.query.filter(
        db.or_(
            Order.status == 'out_for_delivery',
            Order.status == 'shipped',
            Order.status == 'in_transit'
        )
    ).count()
    
    delivered_orders = Order.query.filter_by(status='delivered').count()
    completed_orders = Order.query.filter_by(status='completed').count()
    cancelled_orders = Order.query.filter_by(status='cancelled').count()
    
    # Issues count
    issue_count = Order.query.filter(
        db.or_(
            Order.status == 'issue',
            Order.status == 'issue_reported'
        )
    ).count()
    
    # UCU emails count
    total_ucu_emails = UCUEmail.query.count()
    
    # For chart data
    revenue_labels = []
    revenue_data = []
    today = datetime.utcnow()
    for i in range(29, -1, -1):
        date = today - timedelta(days=i)
        revenue_labels.append(date.strftime('%d %b'))
        
        day_revenue = db.session.query(db.func.sum(Order.total_amount)).filter(
            Order.status == 'completed',
            Order.created_at >= date.replace(hour=0, minute=0, second=0),
            Order.created_at < date.replace(hour=23, minute=59, second=59)
        ).scalar() or 0
        revenue_data.append(day_revenue)
    
    # DEBUG PRINTS - Add these temporarily
    print("="*50)
    print("🔍 ADMIN DASHBOARD LOADED")
    print(f"out_for_delivery_orders: {out_for_delivery_orders}")
    print(f"pending: {pending_orders}, confirmed: {confirmed_orders}, delivered: {delivered_orders}")
    print("="*50)
    
    return render_template('admin_dashboard.html',
                         user=user,
                         total_users=total_users,
                         total_sellers=total_sellers,
                         total_buyers=total_buyers,
                         total_admins=total_admins,
                         total_products=total_products,
                         total_orders=total_orders,
                         total_revenue=total_revenue,
                         recent_orders=recent_orders,
                         recent_users=recent_users,
                         new_users_week=new_users_week,
                         new_sellers_week=new_sellers_week,
                         active_products=active_products,
                         pending_orders=pending_orders,
                         confirmed_orders=confirmed_orders,
                         processing_orders=processing_orders,
                         out_for_delivery_orders=out_for_delivery_orders,
                         delivered_orders=delivered_orders,
                         completed_orders=completed_orders,
                         cancelled_orders=cancelled_orders,
                         issue_count=issue_count,
                         total_ucu_emails=total_ucu_emails,
                         revenue_labels=revenue_labels,
                         revenue_data=revenue_data)



# Add this to your app.py - API endpoint for pending orders count
@app.route('/api/seller/pending-orders-count')
@login_required
def seller_pending_orders_count():
    """Get pending orders count for current seller"""
    try:
        user = get_current_user()
        if user.user_type != 'seller':
            return jsonify({'count': 0, 'success': False}), 403
        
        # Count pending orders for this seller
        pending_count = db.session.query(Order).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status.in_(['pending', 'confirmed', 'processing'])
        ).distinct(Order.id).count()
        
        return jsonify({'count': pending_count, 'success': True})
    except Exception as e:
        print(f"Error getting pending orders count: {e}")
        return jsonify({'count': 0, 'success': False}), 500



@app.route('/api/admin/messages/unread-count')
@admin_required
def api_admin_unread_count():
    """Get unread message count for admin"""
    try:
        admin_id = session['user_id']
        
        # Get all conversations for admin
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == admin_id, Conversation.participant1_type == 'admin'),
                db.and_(Conversation.participant2_id == admin_id, Conversation.participant2_type == 'admin')
            )
        ).all()
        
        total_unread = 0
        for conv in conversations:
            unread = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != admin_id,
                Message.is_read == False
            ).count()
            total_unread += unread
        
        return jsonify({'success': True, 'count': total_unread})
        
    except Exception as e:
        print(f"Error getting unread count: {e}")
        return jsonify({'success': False, 'count': 0}), 500

@app.route('/api/admin/messages/stats')
@admin_required
def api_admin_messages_stats():
    """Get message statistics for admin"""
    try:
        admin_id = session['user_id']
        
        # Total conversations with sellers
        total_conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == admin_id, Conversation.participant1_type == 'admin'),
                db.and_(Conversation.participant2_id == admin_id, Conversation.participant2_type == 'admin')
            )
        ).count()
        
        # Unread count
        unread_count = 0
        conversations = Conversation.query.filter(
            db.or_(
                db.and_(Conversation.participant1_id == admin_id, Conversation.participant1_type == 'admin'),
                db.and_(Conversation.participant2_id == admin_id, Conversation.participant2_type == 'admin')
            )
        ).all()
        
        for conv in conversations:
            unread = Message.query.filter(
                Message.conversation_id == conv.id,
                Message.sender_id != admin_id,
                Message.is_read == False
            ).count()
            unread_count += unread
        
        # Active sellers (with conversations)
        active_sellers = set()
        for conv in conversations:
            if conv.participant1_type == 'seller':
                active_sellers.add(conv.participant1_id)
            if conv.participant2_type == 'seller':
                active_sellers.add(conv.participant2_id)
        
        return jsonify({
            'success': True,
            'total_conversations': total_conversations,
            'unread_count': unread_count,
            'active_sellers': len(active_sellers)
        })
        
    except Exception as e:
        print(f"Error getting stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/sellers/<int:seller_id>')
@admin_required
def api_admin_seller_profile(seller_id):
    """Get seller profile for modal"""
    try:
        seller = User.query.get_or_404(seller_id)
        
        # Get seller stats
        products_count = Product.query.filter_by(seller_id=seller.id, is_active=True).count()
        total_orders = OrderItem.query.filter_by(seller_id=seller.id).distinct(OrderItem.order_id).count()
        total_revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).filter(
            OrderItem.seller_id == seller.id,
            OrderItem.order.has(status='completed')
        ).scalar() or 0
        
        return jsonify({
            'id': seller.id,
            'fullname': seller.fullname,
            'email': seller.email,
            'phone': seller.phone,
            'business_name': seller.business_name,
            'business_address': seller.business_address,
            'nin': seller.nin,
            'seller_rating': seller.seller_rating,
            'is_active': seller.is_active,
            'products_count': products_count,
            'orders_count': total_orders,
            'total_revenue': total_revenue,
            'joined': seller.created_at.strftime('%d %b %Y') if seller.created_at else 'N/A'
        })
        
    except Exception as e:
        print(f"Error getting seller profile: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/seller/analytics')
@login_required
def seller_analytics():
    """Seller analytics with real data analysis"""
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('Access denied. Seller account required.', 'danger')
        return redirect(url_for('products'))
    
    # Get date range from request or default to last 30 days
    end_date = datetime.utcnow().date()
    start_date = request.args.get('start_date')
    end_date_param = request.args.get('end_date')
    
    if start_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    else:
        start_date = end_date - timedelta(days=30)
    
    if end_date_param:
        end_date = datetime.strptime(end_date_param, '%Y-%m-%d').date()
    
    # Convert to datetime for queries
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Calculate previous period for comparison
    period_days = (end_date - start_date).days
    previous_start = start_date - timedelta(days=period_days)
    previous_end = start_date - timedelta(days=1)
    previous_start_datetime = datetime.combine(previous_start, datetime.min.time())
    previous_end_datetime = datetime.combine(previous_end, datetime.max.time())
    
    try:
        # ===== CURRENT PERIOD DATA =====
        
        # Total Revenue
        current_revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).scalar() or 0
        
        # Total Orders
        current_orders = db.session.query(
            func.count(db.distinct(Order.id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).scalar() or 0
        
        # Products Sold
        current_products_sold = db.session.query(
            func.sum(OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).scalar() or 0
        
        # Store Rating
        all_reviews = db.session.query(Review).join(Product).filter(
            Product.seller_id == user.id
        ).all()
        
        if all_reviews:
            store_rating = sum(r.rating for r in all_reviews) / len(all_reviews)
            reviews_count = len(all_reviews)
        else:
            store_rating = 0
            reviews_count = 0
        
        # Unique Customers
        unique_customers = db.session.query(
            func.count(db.distinct(Order.user_id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).scalar() or 0
        
        # Active Products
        active_products = Product.query.filter_by(
            seller_id=user.id, 
            is_active=True
        ).count()
        
        # Store Views (if you have view tracking)
        store_views = 0  # Implement if you have a ProductView model
        
        # Conversion Rate (if you have view tracking)
        conversion_rate = 0  # Implement if you have view tracking
        
        # ===== PREVIOUS PERIOD DATA (for comparison) =====
        
        # Previous Revenue
        previous_revenue = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= previous_start_datetime,
            Order.created_at <= previous_end_datetime
        ).scalar() or 0
        
        # Previous Orders
        previous_orders = db.session.query(
            func.count(db.distinct(Order.id))
        ).join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= previous_start_datetime,
            Order.created_at <= previous_end_datetime
        ).scalar() or 0
        
        # Calculate percentage changes
        revenue_change = ((current_revenue - previous_revenue) / previous_revenue * 100) if previous_revenue > 0 else 0
        orders_change = ((current_orders - previous_orders) / previous_orders * 100) if previous_orders > 0 else 0
        
        # ===== CHART DATA =====
        
        # Daily revenue for line chart (last 30 days)
        daily_labels = []
        daily_data = []
        
        for i in range(30):
            date = end_date - timedelta(days=29-i)
            day_start = datetime.combine(date, datetime.min.time())
            day_end = datetime.combine(date, datetime.max.time())
            
            daily_revenue = db.session.query(
                func.sum(OrderItem.price * OrderItem.quantity)
            ).join(Order).filter(
                OrderItem.seller_id == user.id,
                Order.status == 'completed',
                Order.created_at >= day_start,
                Order.created_at <= day_end
            ).scalar() or 0
            
            daily_labels.append(date.strftime('%d %b'))
            daily_data.append(float(daily_revenue))
        
        # Category sales data
        category_sales = db.session.query(
            Product.category,
            func.sum(OrderItem.price * OrderItem.quantity).label('revenue'),
            func.sum(OrderItem.quantity).label('quantity')
        ).join(OrderItem, Product.id == OrderItem.product_id
        ).join(Order, OrderItem.order_id == Order.id
        ).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).group_by(Product.category).all()
        
        category_data = []
        for cat, rev, qty in category_sales:
            category_data.append({
                'category': cat.replace('_', ' ').title() if cat else 'Other',
                'revenue': float(rev or 0),
                'quantity': qty or 0
            })
        
        # Top selling products
        top_products = db.session.query(
            Product.id,
            Product.name,
            Product.image,
            func.sum(OrderItem.quantity).label('total_sold'),
            func.sum(OrderItem.price * OrderItem.quantity).label('revenue')
        ).join(OrderItem, Product.id == OrderItem.product_id
        ).join(Order, OrderItem.order_id == Order.id
        ).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed',
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).group_by(Product.id, Product.name, Product.image
        ).order_by(func.sum(OrderItem.quantity).desc()
        ).limit(10).all()
        
        top_products_list = []
        for prod in top_products:
            top_products_list.append({
                'id': prod.id,
                'name': prod.name[:40] + '...' if len(prod.name) > 40 else prod.name,
                'total_sold': prod.total_sold or 0,
                'revenue': float(prod.revenue or 0),
                'image': prod.image
            })
        
        # Recent orders
        recent_orders = db.session.query(Order).distinct().join(OrderItem).filter(
            OrderItem.seller_id == user.id,
            Order.created_at >= start_datetime,
            Order.created_at <= end_datetime
        ).order_by(Order.created_at.desc()).limit(10).all()
        
        # Compile analytics data
        analytics_data = {
            'total_revenue': float(current_revenue),
            'revenue_change': float(revenue_change),
            'total_orders': current_orders,
            'orders_change': float(orders_change),
            'products_sold': current_products_sold,
            'store_rating': round(store_rating, 1),
            'reviews_count': reviews_count,
            'store_views': store_views,
            'unique_customers': unique_customers,
            'active_products': active_products,
            'conversion_rate': conversion_rate,
            'line_chart_labels': daily_labels,
            'line_chart_data': daily_data,
            'category_sales': category_data,
            'top_products': top_products_list,
            'recent_orders': recent_orders
        }
        
        print(f"✅ Analytics loaded - Revenue: UGX {current_revenue:,.0f}, Orders: {current_orders}, Products: {current_products_sold}")
        
    except Exception as e:
        print(f"❌ Error loading analytics: {e}")
        traceback.print_exc()
        
        # Default empty data
        analytics_data = {
            'total_revenue': 0,
            'revenue_change': 0,
            'total_orders': 0,
            'orders_change': 0,
            'products_sold': 0,
            'store_rating': 0,
            'reviews_count': 0,
            'store_views': 0,
            'unique_customers': 0,
            'active_products': Product.query.filter_by(seller_id=user.id, is_active=True).count(),
            'conversion_rate': 0,
            'line_chart_labels': [(end_date - timedelta(days=i)).strftime('%d %b') for i in range(29, -1, -1)],
            'line_chart_data': [0] * 30,
            'category_sales': [],
            'top_products': [],
            'recent_orders': []
        }
    
    return render_template('seller_analytics.html',
        user=user,
        analytics_data=analytics_data,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d')
    )



# ==================== LIVE TRACKING API ROUTES ====================





@app.route('/admin/delivery/live-tracking')
@admin_required
def live_tracking_dashboard():
    """Live tracking dashboard for all active deliveries"""
    user = get_current_user()
    
    # Get all active deliveries (orders that are assigned and in progress)
    active_deliveries = Order.query.filter(
        Order.status.in_(['shipped', 'out_for_delivery', 'in_transit']),
        Order.delivery_person_id.isnot(None)
    ).order_by(Order.updated_at.desc()).all()
    
    # Get all delivery persons
    delivery_persons = DeliveryPerson.query.all()
    
    # Calculate statistics
    today = datetime.utcnow().replace(hour=0, minute=0, second=0)
    
    # Active deliveries count
    active_count = len(active_deliveries)
    
    # Completed today
    completed_today_count = Order.query.filter(
        Order.status == 'delivered',
        Order.actual_delivery >= today
    ).count()
    
    # Pending deliveries (assigned but not yet picked up)
    pending_deliveries_count = Order.query.filter(
        Order.status == 'shipped',
        Order.delivery_person_id.isnot(None)
    ).count()
    
    # Online riders (those with current_location)
    online_riders_count = DeliveryPerson.query.filter(
        DeliveryPerson.current_location.isnot(None)
    ).count()
    
    # Prepare data for map with coordinates
    map_data = []
    for delivery in active_deliveries:
        if delivery.delivery_person and delivery.delivery_person.current_location:
            # Try to parse coordinates from location string
            lat = None
            lng = None
            location_str = delivery.delivery_person.current_location
            
            # Check if it's in "lat, lng" format
            if location_str and ',' in location_str:
                try:
                    parts = location_str.split(',')
                    lat = float(parts[0].strip())
                    lng = float(parts[1].strip())
                except:
                    pass
            
            # Get latest tracking point for battery and speed
            latest_tracking = DeliveryTracking.query.filter_by(
                order_id=delivery.id
            ).order_by(DeliveryTracking.created_at.desc()).first()
            
            map_data.append({
                'order_id': delivery.id,
                'rider_name': delivery.delivery_person.name,
                'rider_phone': delivery.delivery_person.phone,
                'location': delivery.delivery_person.current_location,
                'lat': lat,
                'lng': lng,
                'status': delivery.status,
                'customer': delivery.user.fullname if delivery.user else 'N/A',
                'delivery_address': delivery.delivery_address,
                'last_update': latest_tracking.created_at.strftime('%H:%M') if latest_tracking else 'N/A',
                'battery': latest_tracking.battery_level if latest_tracking else None,
                'speed': latest_tracking.speed if latest_tracking else None
            })
    
    return render_template('live_tracking.html',
                         user=user,
                         active_deliveries=active_deliveries,
                         delivery_persons=delivery_persons,
                         map_data=map_data,
                         active_count=active_count,
                         completed_today_count=completed_today_count,
                         pending_deliveries_count=pending_deliveries_count,
                         online_riders_count=online_riders_count,
                         now=datetime.utcnow)



@app.route('/admin/delivery/<int:delivery_id>/tracking-details')
@admin_required
def delivery_tracking_details(delivery_id):
    """View detailed tracking for a specific delivery"""
    delivery = Order.query.get_or_404(delivery_id)
    tracking_points = DeliveryTracking.query.filter_by(order_id=delivery_id).order_by(DeliveryTracking.created_at).all()
    checkpoints = DeliveryCheckpoint.query.filter_by(order_id=delivery_id).all()
    proof = DeliveryProof.query.filter_by(order_id=delivery_id).first()
    
    # Convert tracking_points to dictionaries for JSON serialization
    tracking_points_data = []
    for point in tracking_points:
        tracking_points_data.append({
            'id': point.id,
            'order_id': point.order_id,
            'delivery_person_id': point.delivery_person_id,
            'latitude': point.latitude,
            'longitude': point.longitude,
            'location': point.location_name,  # FIXED: Use location_name instead of location
            'status': point.status,
            'battery_level': point.battery_level,
            'speed': point.speed,
            'accuracy': point.accuracy,
            'created_at': point.created_at.isoformat() if point.created_at else None
        })
    
    # Convert checkpoints to dictionaries
    checkpoints_data = []
    for checkpoint in checkpoints:
        checkpoints_data.append({
            'id': checkpoint.id,
            'order_id': checkpoint.order_id,
            'checkpoint_type': checkpoint.checkpoint_type,
            'location': checkpoint.location,
            'estimated_arrival': checkpoint.estimated_arrival.isoformat() if checkpoint.estimated_arrival else None,
            'actual_arrival': checkpoint.actual_arrival.isoformat() if checkpoint.actual_arrival else None,
            'notes': checkpoint.notes,
            'created_at': checkpoint.created_at.isoformat() if checkpoint.created_at else None
        })
    
    # Calculate statistics using correct field names
    total_time = None
    if delivery.created_at and delivery.actual_delivery:
        time_diff = delivery.actual_delivery - delivery.created_at
        total_time = round(time_diff.total_seconds() / 60)  # in minutes
    
    return render_template('delivery_tracking_details.html',
                         delivery=delivery,
                         tracking_points=tracking_points,
                         tracking_points_data=tracking_points_data,
                         checkpoints=checkpoints,
                         checkpoints_data=checkpoints_data,
                         proof=proof,
                         total_time=total_time,
                         now=datetime.utcnow)


@app.route('/rider/mobile/<int:rider_id>')
@login_required
def rider_mobile_portal(rider_id):
    """Mobile-optimized page for riders to share their location"""
    rider = DeliveryPerson.query.get_or_404(rider_id)
    
    # Get active assignments for this rider - FIXED
    active_assignments = DeliveryAssignment.query.filter(
        DeliveryAssignment.delivery_person_id == rider_id,
        DeliveryAssignment.status.in_(['assigned', 'picked_up', 'in_transit'])
    ).all()
    
    return render_template('rider_mobile.html',
                         rider=rider,
                         active_assignments=active_assignments)



@app.route('/api/rider/update-location', methods=['POST'])
@login_required
def update_rider_location():
    """API endpoint for riders to update their location"""
    try:
        data = request.get_json()
        
        rider_id = data.get('rider_id')
        order_id = data.get('order_id')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        accuracy = data.get('accuracy')
        speed = data.get('speed')
        battery = data.get('battery')
        
        # Get rider
        rider = DeliveryPerson.query.get(rider_id)
        if not rider:
            return jsonify({'success': False, 'error': 'Rider not found'}), 404
        
        # Store location as "lat, lng" format for backward compatibility
        rider.current_location = f"{latitude}, {longitude}"
        rider.updated_at = datetime.utcnow()
        
        # Create tracking point
        tracking = DeliveryTracking(
            order_id=order_id,
            delivery_person_id=rider_id,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            speed=speed,
            battery_level=battery,
            status='in_transit',
            created_at=datetime.utcnow()
        )
        db.session.add(tracking)
        
        # Update order's last_updated
        order = Order.query.get(order_id)
        if order:
            order.last_updated = datetime.utcnow()
            order.current_location = f"{latitude}, {longitude}"
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating location: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/rider/update-status', methods=['POST'])
@login_required
def update_rider_status():
    """API endpoint for riders to update delivery status"""
    try:
        data = request.get_json()
        
        rider_id = data.get('rider_id')
        order_id = data.get('order_id')
        new_status = data.get('status')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        
        order = Order.query.get_or_404(order_id)
        
        # Verify this rider is assigned
        if order.delivery_person_id != rider_id:
            return jsonify({'success': False, 'error': 'Not authorized'}), 403
        
        # Update order status
        order.status = new_status
        order.last_updated = datetime.utcnow()
        
        if new_status == 'delivered':
            order.actual_delivery = datetime.utcnow()
        
        # Create tracking point
        tracking = DeliveryTracking(
            order_id=order_id,
            delivery_person_id=rider_id,
            latitude=latitude,
            longitude=longitude,
            status=new_status,
            created_at=datetime.utcnow()
        )
        db.session.add(tracking)
        
        # Update assignment
        assignment = DeliveryAssignment.query.filter_by(order_id=order_id).first()
        if assignment:
            assignment.status = new_status
            if new_status == 'delivered':
                assignment.completed_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500






@app.route('/create-test-delivery-data')
@admin_required
def create_test_delivery_data():
    """Create test delivery data for live tracking"""
    try:
        # Create a test rider if none exists
        rider = DeliveryPerson.query.filter_by(phone='0700123456').first()
        if not rider:
            rider = DeliveryPerson(
                name='Test Rider',
                phone='0700123456',
                vehicle_type='motorcycle',
                vehicle_number='UBA 123A',
                current_location='0.3136, 32.5811',  # Kampala coordinates
                is_active=True
            )
            db.session.add(rider)
            db.session.commit()
            print(f"✅ Created test rider: {rider.name}")
        
        # Create a test order with this rider
        # Find a buyer user
        buyer = User.query.filter_by(user_type='buyer').first()
        if not buyer:
            # Create a test buyer if none exists
            buyer = User(
                fullname='Test Buyer',
                email='testbuyer@example.com',
                phone='0700123457',
                user_type='buyer',
                is_active=True
            )
            db.session.add(buyer)
            db.session.commit()
        
        # Create test order
        test_order = Order(
            total_amount=25000,
            status='in_transit',
            delivery_address='Makerere University, Kampala',
            payment_method='cash_on_delivery',
            user_id=buyer.id,
            delivery_person_id=rider.id,
            current_location='0.3136, 32.5811',
            last_updated=datetime.utcnow()
        )
        db.session.add(test_order)
        db.session.commit()
        
        # Create tracking points
        for i in range(5):
            tracking = DeliveryTracking(
                order_id=test_order.id,
                delivery_person_id=rider.id,
                latitude=0.3136 + (i * 0.001),
                longitude=32.5811 + (i * 0.001),
                status='in_transit',
                battery_level=85 - (i * 5),
                speed=25 + (i * 2),
                created_at=datetime.utcnow() - timedelta(minutes=5*i)
            )
            db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Test delivery data created',
            'rider_id': rider.id,
            'order_id': test_order.id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500






@app.route('/rider-simulator/<int:rider_id>')
@login_required
def rider_simulator(rider_id):
    """Simulator page for testing rider location updates"""
    rider = DeliveryPerson.query.get_or_404(rider_id)
    
    # FIXED: Use filter() instead of filter_by() for complex conditions with in_()
    active_assignments = DeliveryAssignment.query.filter(
        DeliveryAssignment.delivery_person_id == rider_id,
        DeliveryAssignment.status.in_(['assigned', 'picked_up', 'in_transit'])
    ).all()
    
    return render_template('rider_simulator.html', 
                         rider=rider, 
                         assignments=active_assignments)






# ==================== GOOGLE OAUTH CONFIGURATION ====================
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

# Initialize OAuth
oauth = OAuth(app)

# Configure Google OAuth with complete configuration
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        'prompt': 'select_account'
    },
    # Explicitly set the endpoints
    access_token_url='https://oauth2.googleapis.com/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
)

# Alternative simpler configuration if the above doesn't work
# google = oauth.register(
#     name='google',
#     client_id=GOOGLE_CLIENT_ID,
#     client_secret=GOOGLE_CLIENT_SECRET,
#     access_token_url='https://accounts.google.com/o/oauth2/token',
#     access_token_params=None,
#     authorize_url='https://accounts.google.com/o/oauth2/auth',
#     authorize_params=None,
#     api_base_url='https://www.googleapis.com/oauth2/v1/',
#     userinfo_endpoint='https://www.googleapis.com/oauth2/v1/userinfo',
#     client_kwargs={'scope': 'openid email profile'},
# )



@app.route('/google/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
        # Get the authorization token
        token = google.authorize_access_token()
        
        if not token:
            flash('❌ Google authentication failed. No token received.', 'danger')
            return redirect(url_for('unified_register'))
        
        print(f"✅ Token received: {token.keys()}")
        
        # Try to get user info using the token
        userinfo = None
        
        # Method 1: Use the token to get userinfo
        if 'id_token' in token:
            try:
                # Parse the ID token
                userinfo = google.parse_id_token(token)
                print("✅ Got userinfo from ID token")
            except Exception as e:
                print(f"Error parsing ID token: {e}")
        
        # Method 2: Use the userinfo endpoint
        if not userinfo:
            try:
                # Make a direct request to Google's userinfo endpoint
                headers = {'Authorization': f'Bearer {token["access_token"]}'}
                import requests
                response = requests.get('https://www.googleapis.com/oauth2/v1/userinfo', headers=headers)
                if response.status_code == 200:
                    userinfo = response.json()
                    print("✅ Got userinfo from userinfo endpoint")
                else:
                    print(f"Userinfo endpoint returned {response.status_code}")
            except Exception as e:
                print(f"Error getting userinfo: {e}")
        
        if not userinfo:
            flash('❌ Could not retrieve your information from Google.', 'danger')
            return redirect(url_for('unified_register'))
        
        # Get user info from Google
        google_email = userinfo.get('email')
        google_name = userinfo.get('name', 'Google User')
        google_picture = userinfo.get('picture', '')
        
        if not google_email:
            flash('❌ Could not retrieve your email from Google.', 'danger')
            return redirect(url_for('unified_register'))
        
        print(f"✅ Google user authenticated: {google_email}")
        
        # Check if user already exists
        existing_user = User.query.filter_by(email=google_email).first()
        
        if existing_user:
            # User exists - log them in
            session['user_id'] = existing_user.id
            session['user_name'] = existing_user.fullname
            session['user_type'] = existing_user.user_type
            
            # Don't store Google picture URL in database
            if existing_user.profile_image and existing_user.profile_image.startswith(('http://', 'https://')):
                existing_user.profile_image = None
                db.session.commit()
            
            # Merge cart if buyer
            if existing_user.user_type == 'buyer':
                merge_session_cart_with_user(existing_user.id)
            
            flash(f'✅ Welcome back, {existing_user.fullname}!', 'success')
            
            # Redirect based on user type
            if existing_user.user_type == 'seller':
                if not has_active_subscription(existing_user):
                    return redirect(url_for('seller_subscription'))
                return redirect(url_for('dashboard'))
            elif existing_user.user_type == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('home'))
        else:
            # New user - store info in session and redirect to registration
            session['google_email'] = google_email
            session['google_name'] = google_name
            # Don't store the picture URL
            session['google_picture'] = ''
            
            # Check if it's a UCU email through Google
            if google_email.endswith('@students.ucu.ac.ug'):
                # Check if email exists in UCU database
                ucu_record = UCUEmail.query.filter_by(email=google_email, is_active=True).first()
                if ucu_record:
                    session['verified_ucu_email'] = google_email
                    session['verified_ucu_name'] = ucu_record.full_name
                    session['verified_ucu_student'] = ucu_record.student_number
                    session['verified_ucu_dept'] = ucu_record.department
                    session['verified_ucu_type'] = 'student'
                    return redirect(url_for('complete_buyer_registration'))
            
            elif google_email.endswith('@ucu.ac.ug') and not google_email.endswith('@students.ucu.ac.ug'):
                ucu_record = UCUEmail.query.filter_by(email=google_email, is_active=True).first()
                if ucu_record:
                    session['verified_ucu_email'] = google_email
                    session['verified_ucu_name'] = ucu_record.full_name
                    session['verified_ucu_type'] = 'staff'
                    session['verified_ucu_dept'] = ucu_record.department
                    session['verified_ucu_title'] = ucu_record.staff_title
                    return redirect(url_for('complete_staff_registration'))
            
            # For external Google users
            registration_type = session.get('google_registration_type', 'buyer')
            
            if registration_type == 'seller':
                return redirect(url_for('complete_external_registration'))
            else:
                return redirect(url_for('complete_google_buyer_registration'))
    
    except Exception as e:
        print(f"❌ Google callback error: {e}")
        traceback.print_exc()
        flash(f'An error occurred during Google authentication: {str(e)}', 'danger')
        return redirect(url_for('unified_register'))




@app.route('/boda-riders')
@admin_required
def boda_riders():
    """Boda riders management page"""
    user = get_current_user()
    
    try:
        # Get all delivery persons (riders)
        delivery_persons = DeliveryPerson.query.all()
        
        # Get active deliveries (orders that are assigned and in progress)
        active_deliveries = Order.query.filter(
            Order.status.in_(['shipped', 'out_for_delivery']),
            Order.delivery_person_id.isnot(None)
        ).order_by(Order.updated_at.desc()).all()
        
        # Calculate statistics
        total_riders = len(delivery_persons)
        active_riders = sum(1 for p in delivery_persons if p.is_active)
        
        # Count total assignments
        total_assignments = 0
        for person in delivery_persons:
            # Count deliveries assigned to this rider
            assignments = Order.query.filter_by(delivery_person_id=person.id).count()
            total_assignments += assignments
        
        # Count completed assignments
        completed_assignments = Order.query.filter(
            Order.status == 'delivered',
            Order.delivery_person_id.isnot(None)
        ).count()
        
        return render_template('boda_riders.html',
                             user=user,
                             delivery_persons=delivery_persons,
                             active_deliveries=active_deliveries,
                             total_riders=total_riders,
                             active_riders=active_riders,
                             total_assignments=total_assignments,
                             completed_assignments=completed_assignments)
                             
    except Exception as e:
        print(f"Error loading boda riders page: {str(e)}")
        traceback.print_exc()
        flash('Error loading riders data. Please try again.', 'danger')
        return render_template('boda_riders.html',
                             user=user,
                             delivery_persons=[],
                             active_deliveries=[],
                             total_riders=0,
                             active_riders=0,
                             total_assignments=0,
                             completed_assignments=0)














@app.route('/admin/add-boda', methods=['POST'])
@admin_required
def add_boda_rider():
    """Add a new boda rider"""
    try:
        name = request.form.get('name')
        phone = request.form.get('phone')
        vehicle_type = request.form.get('vehicle_type', 'motorcycle')
        
        existing = DeliveryPerson.query.filter_by(phone=phone).first()
        if existing:
            flash('Boda rider with this phone already exists!', 'danger')
            return redirect(url_for('boda_riders'))
        
        rider = DeliveryPerson(
            name=name,
            phone=phone,
            vehicle_type=vehicle_type,
            is_active=True
        )
        
        db.session.add(rider)
        db.session.commit()
        
        flash('Boda rider added successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding boda rider: {str(e)}', 'danger')
    
    return redirect(url_for('boda_riders'))


# ==================== UCU EMAIL MANAGEMENT ROUTES ====================
@app.route('/admin/ucu-emails', methods=['GET', 'POST'])
@admin_required
def manage_ucu_emails():
    """Manage UCU emails in the database"""
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'generate_students':
            count = generate_complete_ucu_data()
            flash(f'✅ Generated {count} sample UCU emails!', 'success')
            
        elif action == 'add_single':
            email = request.form.get('email')
            full_name = request.form.get('full_name')
            user_type = request.form.get('user_type')
            student_number = request.form.get('student_number')
            department = request.form.get('department')
            
            # Validate email format
            if user_type == 'student' and not validate_student_email(email):
                flash('❌ Invalid student email format. Use: B22564@students.ucu.ac.ug', 'danger')
            elif user_type == 'staff' and not validate_staff_email(email):
                flash('❌ Invalid staff email format. Use: surname@ucu.ac.ug', 'danger')
            else:
                try:
                    # Check if email already exists
                    if UCUEmail.query.filter_by(email=email).first():
                        flash(f'❌ Email {email} already exists!', 'danger')
                    else:
                        ucu_email = UCUEmail(
                            email=email,
                            full_name=full_name,
                            user_type=user_type,
                            student_number=student_number if user_type == 'student' else None,
                            department=department
                        )
                        db.session.add(ucu_email)
                        db.session.commit()
                        flash(f'✅ Email {email} added successfully!', 'success')
                except Exception as e:
                    db.session.rollback()
                    flash(f'❌ Error adding email: {str(e)}', 'danger')
        
        elif action == 'upload_csv':
            if 'csv_file' not in request.files:
                flash('No file uploaded', 'danger')
            else:
                file = request.files['csv_file']
                if file.filename.endswith('.csv'):
                    count = import_ucu_emails_from_csv(file)
                    flash(f'✅ Successfully imported {count} emails!', 'success')
                else:
                    flash('❌ Please upload a CSV file', 'danger')
        
        elif action == 'delete_all':
            try:
                count = UCUEmail.query.delete()
                db.session.commit()
                flash(f'✅ Deleted {count} UCU emails', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'❌ Error deleting emails: {str(e)}', 'danger')
    
    # Get statistics
    total_students = UCUEmail.query.filter_by(user_type='student').count()
    total_staff = UCUEmail.query.filter_by(user_type='staff').count()
    total_emails = UCUEmail.query.count()
    
    # Get recent emails with pagination
    page = request.args.get('page', 1, type=int)
    per_page = 50
    pagination = UCUEmail.query.order_by(UCUEmail.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    recent_emails = pagination.items
    
    return render_template('admin_ucu_emails.html',
                         user=get_current_user(),
                         total_students=total_students,
                         total_staff=total_staff,
                         total_emails=total_emails,
                         recent_emails=recent_emails,
                         pagination=pagination,
                         now=datetime.utcnow)

@app.route('/admin/ucu-emails/<int:email_id>/delete', methods=['POST'])
@admin_required
def delete_ucu_email(email_id):
    """Delete a specific UCU email"""
    try:
        email = UCUEmail.query.get_or_404(email_id)
        email_address = email.email
        db.session.delete(email)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Email {email_address} deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/ucu-emails/export')
@admin_required
def export_ucu_emails():
    """Export UCU emails to CSV"""
    try:
        emails = UCUEmail.query.order_by(UCUEmail.created_at.desc()).all()
        
        output = BytesIO()
        # Create a text stream wrapper for CSV writer
        import io
        text_stream = io.TextIOWrapper(output, encoding='utf-8', newline='')
        writer = csv.writer(text_stream)
        
        # Write headers
        writer.writerow(['ID', 'Email', 'Full Name', 'Type', 'Student/Staff ID', 
                         'Department', 'Faculty', 'Year/Title', 'Created At'])
        
        # Write data
        for email in emails:
            if email.user_type == 'student':
                identifier = email.student_number
                year_title = f"Year {email.year_of_study}" if email.year_of_study else 'N/A'
            else:
                identifier = email.staff_title
                year_title = email.staff_title or 'N/A'
            
            writer.writerow([
                email.id,
                email.email,
                email.full_name,
                email.user_type,
                identifier or 'N/A',
                email.department or 'N/A',
                email.faculty or 'N/A',
                year_title,
                email.created_at.strftime('%Y-%m-%d %H:%M') if email.created_at else 'N/A'
            ])
        
        text_stream.flush()
        output.seek(0)
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'ucu_emails_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        )
    except Exception as e:
        print(f"Error exporting UCU emails: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500




# ==================== UCU EMAIL UTILITY ROUTES ====================
@app.route('/admin/setup-ucu-database')
@admin_required
def setup_ucu_database():
    """Set up UCU database with sample data"""
    try:
        count = generate_complete_ucu_data()
        
        return f"""
        <div style="text-align: center; padding: 50px; font-family: Arial;">
            <h1 style="color: green;">✅ UCU Database Created!</h1>
            <p>{count} UCU students and staff have been added to the database.</p>
            <p>You can now test registration with these emails.</p>
            <p><strong>Sample Student:</strong> B22564@students.ucu.ac.ug</p>
            <p><strong>Sample Staff:</strong> okello@ucu.ac.ug</p>
            <p><a href="/admin/ucu-emails" style="background: #ff6b00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">View UCU Database</a></p>
        </div>
        """
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"

@app.route('/admin/reset-ucu-table')
@admin_required
def reset_ucu_table():
    """Reset UCU emails table"""
    try:
        # Drop and recreate the table
        db.session.execute('DROP TABLE IF EXISTS ucu_emails')
        db.session.commit()
        
        # Recreate the table
        db.create_all()
        
        return """
        <div style="text-align: center; padding: 50px;">
            <h1 style="color: green;">✅ UCU Emails Table Reset!</h1>
            <p>The table has been completely reset with the correct schema.</p>
            <p><a href="/admin/setup-ucu-database" style="background: #ff6b00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Now Generate Sample Data</a></p>
        </div>
        """
        
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"

@app.route('/admin/force-recreate-ucu-table')
@admin_required
def force_recreate_ucu_table():
    """Force recreate UCU emails table with sample data"""
    try:
        # Drop the table if it exists
        db.session.execute('DROP TABLE IF EXISTS ucu_emails')
        db.session.commit()
        
        # Recreate the table
        db.create_all()
        
        # Add sample data
        students = [
            ("B22564", "Okello John", "Computer Science", 3, "Faculty of Science and Technology"),
            ("B22789", "Nabatanzi Sarah", "Business Administration", 2, "Faculty of Business"),
            ("B22134", "Kato David", "Law", 4, "Faculty of Law"),
        ]
        
        staff = [
            ("okello", "Dr. Peter Okello", "Computer Science", "Senior Lecturer"),
            ("nakato", "Prof. Grace Nakato", "Law", "Professor"),
            ("ssenyonjo", "Dr. James Ssenyonjo", "Business", "Associate Professor"),
        ]
        
        count = 0
        
        # Add students
        for student_num, name, dept, year, faculty in students:
            email = f"{student_num}@students.ucu.ac.ug"
            student = UCUEmail(
                email=email,
                full_name=name,
                user_type='student',
                student_number=student_num,
                department=dept,
                year_of_study=year,
                faculty=faculty,
                is_active=True
            )
            db.session.add(student)
            count += 1
        
        # Add staff
        for email_prefix, name, dept, title in staff:
            email = f"{email_prefix}@ucu.ac.ug"
            staff_member = UCUEmail(
                email=email,
                full_name=name,
                user_type='staff',
                department=dept,
                faculty=f"Faculty of {dept}",
                staff_title=title,
                is_active=True
            )
            db.session.add(staff_member)
            count += 1
        
        db.session.commit()
        
        return f"""
        <div style="text-align: center; padding: 50px;">
            <h1 style="color: green;">✅ UCU Table Recreated!</h1>
            <p>Added {count} sample UCU emails.</p>
            <p><a href="/admin/ucu-emails" style="background: #ff6b00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">View UCU Emails</a></p>
        </div>
        """
    except Exception as e:
        db.session.rollback()
        return f"<h1>Error: {str(e)}</h1>"





@app.route('/api/orders/<int:order_id>/summary', methods=['GET'])
@login_required
def get_order_summary(order_id):
    """Get order summary for modals/previews"""
    try:
        user_id = session['user_id']
        user = get_current_user()
        
        order = Order.query.get_or_404(order_id)
        
        # Verify access
        if user.user_type == 'buyer' and order.user_id != user_id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        elif user.user_type == 'seller':
            # Check if seller has items in this order
            seller_items = OrderItem.query.filter_by(
                order_id=order_id,
                seller_id=user_id
            ).first()
            if not seller_items:
                return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        items = []
        for item in order.order_items:
            product = item.product
            items.append({
                'id': item.id,
                'product_id': item.product_id,
                'name': product.name if product else 'Product',
                'quantity': item.quantity,
                'price': float(item.price),
                'total': float(item.price * item.quantity),
                'image': url_for('static', filename='uploads/' + product.image) if product and product.image else None
            })
        
        return jsonify({
            'success': True,
            'order_id': order.id,
            'status': order.status,
            'total': float(order.total_amount),
            'items': items,
            'created_at': order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else None
        })
        
    except Exception as e:
        print(f"❌ Error getting order summary: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== DELIVERY TRACKING API ROUTES ====================
@app.route('/boda/simple-update/<int:boda_id>')
@login_required
def boda_simple_update(boda_id):
    """Simple update page for boda riders"""
    boda = DeliveryPerson.query.get_or_404(boda_id)
    
    current_assignments = DeliveryAssignment.query.filter_by(
        delivery_person_id=boda_id,
        status='assigned'
    ).all()
    
    return render_template('boda_simple_update.html',
                         boda=boda,
                         assignments=current_assignments)

@app.route('/api/boda/simple-update', methods=['POST'])
@login_required
def boda_simple_update_api():
    """API endpoint for boda riders to update status"""
    try:
        boda_id = request.form.get('boda_id')
        assignment_id = request.form.get('assignment_id')
        status = request.form.get('status')
        location = request.form.get('location', '')
        
        boda = DeliveryPerson.query.get_or_404(boda_id)
        assignment = DeliveryAssignment.query.get_or_404(assignment_id)
        
        if location:
            boda.current_location = location
        
        assignment.status = status
        
        tracking = OrderTracking(
            order_id=assignment.order_id,
            status=status,
            location=location or boda.current_location,
            notes=f'Boda: {boda.name}'
        )
        db.session.add(tracking)
        
        order = assignment.order
        if status == 'picked_up':
            order.status = 'shipped'
        elif status == 'delivered':
            order.status = 'delivered'
            order.actual_delivery = datetime.utcnow()
            assignment.completed_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Status updated successfully!'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
    





@app.route('/admin/users')
@admin_required
def admin_users():
    """Admin users management"""
    user = get_current_user()
    
    # Get filter parameters
    search = request.args.get('search', '')
    user_type = request.args.get('user_type', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Build query
    query = User.query
    
    if search:
        query = query.filter(
            db.or_(
                User.fullname.ilike(f'%{search}%'),
                User.email.ilike(f'%{search}%'),
                User.phone.ilike(f'%{search}%')
            )
        )
    
    if user_type:
        query = query.filter_by(user_type=user_type)
    
    # Get paginated results
    pagination = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    users = pagination.items
    
    # Get counts
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    inactive_users = total_users - active_users
    seller_count = User.query.filter_by(user_type='seller').count()
    
    return render_template('admin_users.html',
                         user=user,
                         users=users,
                         pagination=pagination,
                         total_users=total_users,
                         active_users=active_users,
                         inactive_users=inactive_users,
                         seller_count=seller_count)






@app.route('/api/admin/sellers/export', methods=['GET'])
@admin_required
def admin_export_sellers():
    """Export sellers to CSV"""
    try:
        # Get filter parameters
        search = request.args.get('search', '')
        status = request.args.get('status', '')
        verification = request.args.get('verification', '')
        
        # Build query
        query = User.query.filter_by(user_type='seller')
        
        if search:
            query = query.filter(
                db.or_(
                    User.fullname.ilike(f'%{search}%'),
                    User.email.ilike(f'%{search}%'),
                    User.business_name.ilike(f'%{search}%')
                )
            )
        
        if status == 'active':
            query = query.filter_by(is_active=True)
        elif status == 'inactive':
            query = query.filter_by(is_active=False)
        
        if verification == 'verified':
            query = query.filter(User.nin.isnot(None))
        elif verification == 'unverified':
            query = query.filter(User.nin.is_(None))
        
        sellers = query.order_by(User.created_at.desc()).all()
        
        # Create CSV
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow(['ID', 'Name', 'Email', 'Business', 'Phone', 'NIN', 'Products', 'Revenue', 'Rating', 'Status', 'Joined'])
        
        # Write data
        for seller in sellers:
            # Get products count
            products_count = Product.query.filter_by(seller_id=seller.id).count()
            
            # Get revenue
            revenue = db.session.query(
                func.sum(OrderItem.price * OrderItem.quantity)
            ).join(Order).filter(
                OrderItem.seller_id == seller.id,
                Order.status == 'completed'
            ).scalar() or 0
            
            writer.writerow([
                seller.id,
                seller.fullname,
                seller.email,
                seller.business_name or 'N/A',
                seller.phone or 'N/A',
                seller.nin or 'N/A',
                products_count,
                f"{revenue:,.0f}",
                seller.seller_rating or 0,
                'Active' if seller.is_active else 'Inactive',
                seller.created_at.strftime('%Y-%m-%d') if seller.created_at else 'N/A'
            ])
        
        # Create response
        csv_content = output.getvalue()
        output.close()
        
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=sellers_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error exporting sellers: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500




# ==================== ADMIN USER MANAGEMENT ROUTES ====================
@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_user(user_id):
    """Edit user page - handles both GET and POST"""
    user = get_current_user()
    target_user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        try:
            # Update basic info
            target_user.fullname = request.form.get('fullname', target_user.fullname)
            target_user.phone = request.form.get('phone', target_user.phone)
            target_user.location = request.form.get('location', target_user.location)
            
            # Update user type (only if not self)
            if target_user.id != user.id:
                new_user_type = request.form.get('user_type')
                if new_user_type in ['buyer', 'seller', 'staff', 'admin']:
                    target_user.user_type = new_user_type
            
            # Handle profile image upload
            if 'profile_image' in request.files:
                file = request.files['profile_image']
                if file and file.filename and allowed_file(file.filename):
                    # Delete old image if exists
                    if target_user.profile_image:
                        old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], target_user.profile_image)
                        if os.path.exists(old_image_path):
                            os.remove(old_image_path)
                    
                    # Save new image
                    filename = secure_filename(file.filename)
                    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
                    new_filename = timestamp + filename
                    ensure_upload_folder()
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], new_filename))
                    target_user.profile_image = new_filename
            
            # Buyer-specific fields
            if target_user.user_type == 'buyer':
                target_user.delivery_address = request.form.get('delivery_address', target_user.delivery_address)
            
            # Seller-specific fields
            if target_user.user_type == 'seller':
                target_user.business_name = request.form.get('business_name', target_user.business_name)
                target_user.business_address = request.form.get('business_address', target_user.business_address)
                target_user.nin = request.form.get('nin', target_user.nin)
            
            target_user.updated_at = datetime.utcnow()
            db.session.commit()
            
            flash(f'✅ User {target_user.fullname} updated successfully.', 'success')
            return redirect(url_for('admin_edit_user', user_id=target_user.id))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error updating user: {str(e)}")
            traceback.print_exc()
            flash(f'❌ Error updating user: {str(e)}', 'danger')
            return redirect(url_for('admin_edit_user', user_id=target_user.id))
    
    # GET request - show edit form
    return render_template('admin_edit_user.html', user=user, target_user=target_user)









@app.route('/admin/settings')
@admin_required
def admin_settings():
    """Admin settings page"""
    user = get_current_user()
    return render_template('admin_settings.html', user=user, now=datetime.utcnow)




@app.route('/api/admin/products/<int:product_id>')
@admin_required
def api_admin_product(product_id):
    """Get product details for API"""
    product = Product.query.get_or_404(product_id)
    
    return jsonify({
        'id': product.id,
        'name': product.name,
        'description': product.description,
        'price': product.price,
        'price_formatted': f"{product.price:,.0f}",
        'stock': product.stock,
        'category': product.category,
        'brand': product.brand,
        'condition': product.condition,
        'image': url_for('static', filename='uploads/' + product.image) if product.image else None,
        'is_active': product.is_active,
        'rating': product.rating,
        'sold_count': product.sold_count,
        'seller_name': product.seller.business_name or product.seller.fullname if product.seller else 'N/A',
        'created_at': product.created_at.strftime('%d %b %Y') if product.created_at else 'N/A'
    })


@app.route('/api/admin/sellers/<int:seller_id>')
@admin_required
def api_admin_seller(seller_id):
    """Get seller details for API"""
    seller = User.query.get_or_404(seller_id)
    
    # Get seller stats
    products_count = Product.query.filter_by(seller_id=seller.id).count()
    orders_count = OrderItem.query.filter_by(seller_id=seller.id).distinct(OrderItem.order_id).count()
    
    total_revenue = db.session.query(db.func.sum(OrderItem.price * OrderItem.quantity)).filter(
        OrderItem.seller_id == seller.id
    ).scalar() or 0
    
    return jsonify({
        'id': seller.id,
        'fullname': seller.fullname,
        'email': seller.email,
        'phone': seller.phone,
        'business_name': seller.business_name,
        'business_address': seller.business_address,
        'nin': seller.nin,
        'seller_rating': seller.seller_rating,
        'is_active': seller.is_active,
        'created_at': seller.created_at.isoformat() if seller.created_at else None,
        'products_count': products_count,
        'orders_count': orders_count,
        'total_revenue': total_revenue
    })


@app.route('/api/admin/sellers/<int:seller_id>/approve', methods=['POST'])
@admin_required
def api_admin_approve_seller(seller_id):
    """Approve seller"""
    try:
        seller = User.query.get_or_404(seller_id)
        seller.is_active = True
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/sellers/<int:seller_id>/toggle-status', methods=['POST'])
@admin_required
def api_admin_toggle_seller(seller_id):
    """Toggle seller status"""
    try:
        data = request.get_json()
        seller = User.query.get_or_404(seller_id)
        
        if data.get('action') == 'activate':
            seller.is_active = True
        elif data.get('action') == 'deactivate':
            seller.is_active = False
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/sellers/export')
@admin_required
def api_admin_export_sellers():
    """Export sellers to CSV"""
    # Get filter parameters
    search = request.args.get('search', '')
    status = request.args.get('status', '')
    verification = request.args.get('verification', '')
    
    # Build query
    query = User.query.filter_by(user_type='seller')
    
    if search:
        query = query.filter(
            db.or_(
                User.fullname.ilike(f'%{search}%'),
                User.email.ilike(f'%{search}%'),
                User.business_name.ilike(f'%{search}%')
            )
        )
    
    if status == 'active':
        query = query.filter_by(is_active=True)
    elif status == 'inactive':
        query = query.filter_by(is_active=False)
    
    if verification == 'verified':
        query = query.filter(User.nin.isnot(None))
    elif verification == 'unverified':
        query = query.filter(User.nin.is_(None))
    
    sellers = query.order_by(User.created_at.desc()).all()
    
    # Create CSV
    output = BytesIO()
    writer = csv.writer(output)
    
    writer.writerow(['ID', 'Name', 'Email', 'Business', 'Phone', 'NIN', 'Products', 'Rating', 'Status', 'Joined'])
    
    for seller in sellers:
        products_count = Product.query.filter_by(seller_id=seller.id).count()
        
        writer.writerow([
            seller.id,
            seller.fullname,
            seller.email,
            seller.business_name or 'N/A',
            seller.phone or 'N/A',
            seller.nin or 'N/A',
            products_count,
            seller.seller_rating or 0,
            'Active' if seller.is_active else 'Inactive',
            seller.created_at.strftime('%Y-%m-%d') if seller.created_at else 'N/A'
        ])
    
    output.seek(0)
    
    return send_file(
        output,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'sellers_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
    )




@app.route('/admin/tracking')
@admin_required
def admin_tracking():
    """Admin order tracking page"""
    user = get_current_user()
    
    # Get all active orders (confirmed, processing, out_for_delivery)
    active_orders = Order.query.filter(
        Order.status.in_(['confirmed', 'processing', 'out_for_delivery'])
    ).order_by(Order.created_at.desc()).all()
    
    # Get all delivery persons
    delivery_persons = DeliveryPerson.query.filter_by(is_active=True).all()
    
    # Get assigned orders with delivery details
    assigned_orders = []
    for order in active_orders:
        assignment = DeliveryAssignment.query.filter_by(order_id=order.id).first()
        if assignment:
            assigned_orders.append({
                'order': order,
                'assignment': assignment,
                'delivery_person': assignment.delivery_person
            })
    
    # Get recent tracking updates - FIXED: Add this line
    recent_tracking_updates = OrderTracking.query.order_by(
        OrderTracking.created_at.desc()
    ).limit(10).all()
    
    # If OrderTracking doesn't exist or is empty, use an empty list
    if recent_tracking_updates is None:
        recent_tracking_updates = []
    
    return render_template('admin_tracking.html',
                         user=user,
                         active_orders=active_orders,
                         delivery_persons=delivery_persons,
                         assigned_orders=assigned_orders,
                         recent_tracking_updates=recent_tracking_updates)  # Add this line




@app.route('/admin/reports')
@admin_required
def admin_reports():
    """Admin reports page"""
    user = get_current_user()
    
    # Get current date for the template
    now = datetime.utcnow
    
    # Get counts for quick stats
    total_users = User.query.count()
    total_sellers = User.query.filter_by(user_type='seller').count()
    total_orders = Order.query.count()
    total_products = Product.query.count()
    
    # Get additional stats
    total_buyers = User.query.filter_by(user_type='buyer').count()
    total_staff = User.query.filter_by(user_type='staff').count()
    total_admins = User.query.filter_by(user_type='admin').count()
    
    # Product stats
    active_products = Product.query.filter_by(is_active=True).count()
    inactive_products = Product.query.filter_by(is_active=False).count()
    
    # Seller stats
    active_sellers = User.query.filter_by(user_type='seller', is_active=True).count()
    inactive_sellers = User.query.filter_by(user_type='seller', is_active=False).count()
    
    # Calculate revenue growth (this month vs last month)
    today = datetime.utcnow()
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    
    current_month_revenue = db.session.query(db.func.sum(Order.total_amount)).filter(
        Order.status == 'completed',
        Order.created_at >= month_start
    ).scalar() or 0
    
    last_month_revenue = db.session.query(db.func.sum(Order.total_amount)).filter(
        Order.status == 'completed',
        Order.created_at >= last_month_start,
        Order.created_at < month_start
    ).scalar() or 0
    
    revenue_growth = ((current_month_revenue - last_month_revenue) / last_month_revenue * 100) if last_month_revenue > 0 else 0
    
    # Get current month orders
    current_month_orders = Order.query.filter(Order.created_at >= month_start).count()
    
    # Get last month orders
    last_month_orders = Order.query.filter(
        Order.created_at >= last_month_start,
        Order.created_at < month_start
    ).count()
    
    order_growth = ((current_month_orders - last_month_orders) / last_month_orders * 100) if last_month_orders > 0 else 0
    
    return render_template('admin_reports.html',
                         user=user,
                         now=now,
                         total_users=total_users,
                         total_sellers=total_sellers,
                         total_buyers=total_buyers,
                         total_staff=total_staff,
                         total_admins=total_admins,
                         total_orders=total_orders,
                         total_products=total_products,
                         active_products=active_products,
                         inactive_products=inactive_products,
                         active_sellers=active_sellers,
                         inactive_sellers=inactive_sellers,
                         current_month_revenue=current_month_revenue,
                         revenue_growth=revenue_growth,
                         current_month_orders=current_month_orders,
                         order_growth=order_growth)





@app.route('/chat')
@login_required
def chat():
    """Main chat page for all users"""
    user = get_current_user()
    
    # Redirect to appropriate specialized chat if needed
    if user.user_type == 'seller':
        return redirect(url_for('seller_messages'))
    elif user.user_type == 'admin':
        return redirect(url_for('admin_messages'))
    else:
        # For buyers, show the chat page
        return render_template('chat.html')




@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Handle forgot password requests with proper email handling"""
    if request.method == 'POST':
        # Check if it's an AJAX request
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        email = request.form.get('email', '').strip().lower()
        
        if not email:
            if is_ajax:
                return jsonify({'success': False, 'message': 'Please enter your email address.', 'type': 'danger'})
            else:
                flash('Please enter your email address.', 'danger')
                return render_template('forgot_password.html')
        
        # Check if user exists (don't reveal this for security, but we need it for the email)
        user = User.query.filter_by(email=email).first()
        
        # Always show success message even if email doesn't exist (security best practice)
        if not user:
            print(f"Password reset requested for non-existent email: {email}")
            if is_ajax:
                return jsonify({
                    'success': True, 
                    'message': 'If your email is registered, you will receive a password reset link shortly.'
                })
            else:
                flash('If your email is registered, you will receive a password reset link shortly.', 'success')
                return render_template('forgot_password.html')
        
        try:
            # Generate reset token
            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=1)
            
            # Delete any existing unused tokens for this email
            PasswordReset.query.filter_by(email=email, used=False).delete()
            
            # Create new reset token
            reset = PasswordReset(
                email=email,
                token=token,
                expires_at=expires_at
            )
            db.session.add(reset)
            db.session.commit()
            
            print(f"✅ Password reset token generated for {email}")
            
            # Send email with better error handling
            email_sent = send_password_reset_email(email, token, user.fullname)
            
            if email_sent:
                print(f"✅ Password reset email sent to {email}")
                if is_ajax:
                    return jsonify({
                        'success': True,
                        'message': 'Password reset link has been sent to your email.'
                    })
                else:
                    flash('Password reset link has been sent to your email.', 'success')
            else:
                print(f"❌ Failed to send password reset email to {email}")
                if is_ajax:
                    return jsonify({
                        'success': False,
                        'message': 'Error sending email. Please try again later or contact support.',
                        'type': 'danger'
                    })
                else:
                    flash('Error sending email. Please try again later or contact support.', 'danger')
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error in forgot_password: {e}")
            traceback.print_exc()
            
            if is_ajax:
                return jsonify({
                    'success': False,
                    'message': 'An error occurred. Please try again.',
                    'type': 'danger'
                })
            else:
                flash('An error occurred. Please try again.', 'danger')
        
        return render_template('forgot_password.html')
    
    # GET request - show form
    return render_template('forgot_password.html')




# ==================== ORDER RETURN ROUTES ====================

@app.route('/api/order/request-return', methods=['POST'])
@login_required
def request_order_return():
    """API endpoint for buyers to request returns"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        order_id = data.get('order_id')
        reason = data.get('reason')
        description = data.get('description')
        
        if not order_id:
            return jsonify({'success': False, 'message': 'Order ID required'}), 400
        
        order = Order.query.get_or_404(order_id)
        
        # Verify user owns this order
        if order.user_id != user_id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        # Check if order is within return window (e.g., 7 days)
        if order.created_at < datetime.utcnow() - timedelta(days=7):
            return jsonify({'success': False, 'message': 'Return window has expired (7 days)'}), 400
        
        # Update order status
        order.status = 'return_requested'
        
        # Add tracking update
        tracking = OrderTracking(
            order_id=order_id,
            status='return_requested',
            notes=f'Return Reason: {reason}\nDescription: {description}'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        print(f"🔄 Return requested for Order #{order_id} by User #{user_id}")
        print(f"   Reason: {reason}")
        print(f"   Description: {description}")
        
        return jsonify({
            'success': True,
            'message': 'Return request submitted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error requesting return: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500



# ==================== ORDER CANCELLATION ROUTES ====================

@app.route('/api/order/cancel', methods=['POST'])
@login_required
def cancel_order():
    """API endpoint for buyers to cancel orders"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        order_id = data.get('order_id')
        
        if not order_id:
            return jsonify({'success': False, 'message': 'Order ID required'}), 400
        
        order = Order.query.get_or_404(order_id)
        
        # Verify user owns this order
        if order.user_id != user_id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        # Only pending orders can be cancelled
        if order.status != 'pending':
            return jsonify({'success': False, 'message': 'Only pending orders can be cancelled'}), 400
        
        # Update order status
        order.status = 'cancelled'
        
        # Restore product stock
        for item in order.order_items:
            product = Product.query.get(item.product_id)
            if product:
                product.stock += item.quantity
                product.sold_count -= item.quantity
        
        # Add tracking update
        tracking = OrderTracking(
            order_id=order_id,
            status='cancelled',
            notes='Order cancelled by buyer'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Order cancelled successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error cancelling order: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500



@app.route('/orders')
@login_required
def orders():
    """Display orders for buyers and sellers"""
    user = get_current_user()
    
    try:
        if user.user_type == 'buyer':
            # Get orders for buyer
            orders = Order.query.filter_by(user_id=user.id)\
                .options(
                    db.joinedload(Order.user),
                    db.joinedload(Order.order_items).joinedload(OrderItem.product),
                    db.joinedload(Order.order_items).joinedload(OrderItem.seller)
                )\
                .order_by(Order.created_at.desc())\
                .all()
            
            print(f"✅ Found {len(orders)} orders for buyer {user.id}")
            
        elif user.user_type == 'seller':
            # Get orders where seller has items
            orders = Order.query\
                .join(OrderItem)\
                .filter(OrderItem.seller_id == user.id)\
                .options(
                    db.joinedload(Order.user),
                    db.joinedload(Order.order_items).joinedload(OrderItem.product),
                    db.joinedload(Order.order_items).joinedload(OrderItem.seller)
                )\
                .distinct()\
                .order_by(Order.created_at.desc())\
                .all()
            
            print(f"✅ Found {len(orders)} orders for seller {user.id}")
            
        elif user.user_type == 'admin':
            # Get all orders for admin
            orders = Order.query\
                .options(
                    db.joinedload(Order.user),
                    db.joinedload(Order.order_items).joinedload(OrderItem.product),
                    db.joinedload(Order.order_items).joinedload(OrderItem.seller)
                )\
                .order_by(Order.created_at.desc())\
                .all()
            
            print(f"✅ Found {len(orders)} total orders")
            
        else:
            orders = []
            
        return render_template('orders.html', orders=orders, user=user)
        
    except Exception as e:
        print(f"❌ Error loading orders: {str(e)}")
        traceback.print_exc()
        flash('Error loading your orders. Please try again.', 'danger')
        return render_template('orders.html', orders=[], user=user)

# ==================== REVIEW ROUTES ====================

@app.route('/api/reviews/add', methods=['POST'])
@login_required
def add_review():
    """API endpoint for buyers to add product reviews"""
    try:
        data = request.get_json()
        user_id = session['user_id']
        product_id = data.get('product_id')
        rating = data.get('rating')
        comment = data.get('comment', '').strip()
        
        if not product_id or not rating:
            return jsonify({'success': False, 'message': 'Product ID and rating required'}), 400
        
        # Validate rating
        try:
            rating = int(rating)
            if rating < 1 or rating > 5:
                return jsonify({'success': False, 'message': 'Rating must be between 1 and 5'}), 400
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid rating'}), 400
        
        product = Product.query.get_or_404(product_id)
        
        # Check if user has purchased this product
        has_purchased = db.session.query(Order).join(OrderItem).filter(
            Order.user_id == user_id,
            OrderItem.product_id == product_id,
            Order.status == 'completed'
        ).first()
        
        if not has_purchased:
            return jsonify({'success': False, 'message': 'You can only review products you have purchased'}), 400
        
        # Check if user already reviewed this product
        existing_review = Review.query.filter_by(
            user_id=user_id,
            product_id=product_id
        ).first()
        
        if existing_review:
            # Update existing review
            existing_review.rating = rating
            existing_review.comment = comment
            existing_review.created_at = datetime.utcnow()
            review = existing_review
        else:
            # Create new review
            review = Review(
                user_id=user_id,
                product_id=product_id,
                rating=rating,
                comment=comment,
                created_at=datetime.utcnow()
            )
            db.session.add(review)
        
        db.session.commit()
        
        # Update product rating
        all_reviews = Review.query.filter_by(product_id=product_id).all()
        avg_rating = sum(r.rating for r in all_reviews) / len(all_reviews)
        product.rating = avg_rating
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Review submitted successfully',
            'review': {
                'id': review.id,
                'rating': review.rating,
                'comment': review.comment,
                'created_at': review.created_at.strftime('%Y-%m-%d %H:%M')
            }
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error adding review: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/reviews/product/<int:product_id>', methods=['GET'])
def get_product_reviews(product_id):
    """API endpoint to get reviews for a product"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        reviews = Review.query.filter_by(product_id=product_id)\
            .order_by(Review.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        
        reviews_data = []
        for review in reviews.items:
            reviews_data.append({
                'id': review.id,
                'user_name': review.user.fullname if review.user else 'Anonymous',
                'rating': review.rating,
                'comment': review.comment,
                'created_at': review.created_at.strftime('%d %b %Y')
            })
        
        # Get rating summary
        all_reviews = Review.query.filter_by(product_id=product_id).all()
        total_reviews = len(all_reviews)
        avg_rating = sum(r.rating for r in all_reviews) / total_reviews if total_reviews > 0 else 0
        
        # Rating distribution
        rating_counts = {1:0, 2:0, 3:0, 4:0, 5:0}
        for review in all_reviews:
            rating_counts[review.rating] = rating_counts.get(review.rating, 0) + 1
        
        return jsonify({
            'success': True,
            'reviews': reviews_data,
            'summary': {
                'total': total_reviews,
                'average': round(avg_rating, 1),
                'distribution': rating_counts
            },
            'page': page,
            'pages': reviews.pages,
            'total': reviews.total
        })
        
    except Exception as e:
        print(f"Error getting reviews: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500






# ==================== PENDING DELIVERIES API ====================

@app.route('/api/orders/need-confirmation-count', methods=['GET'])
@login_required
def orders_need_confirmation_count():
    """Get count of orders that need delivery confirmation"""
    try:
        user_id = session['user_id']
        user_type = session['user_type']
        
        if user_type != 'buyer':
            return jsonify({'count': 0, 'success': True})
        
        # Count delivered orders that need confirmation
        count = Order.query.filter_by(
            user_id=user_id,
            status='delivered'
        ).count()
        
        return jsonify({'count': count, 'success': True})
    except Exception as e:
        print(f"Error fetching delivery count: {e}")
        return jsonify({'count': 0, 'success': False})

@app.route('/api/orders/first-pending-delivery', methods=['GET'])
@login_required
def first_pending_delivery():
    """Get the first order needing delivery confirmation"""
    try:
        user_id = session['user_id']
        
        # Get the most recent delivered order that needs confirmation
        order = Order.query.filter_by(
            user_id=user_id,
            status='delivered'
        ).order_by(Order.created_at.desc()).first()
        
        return jsonify({
            'success': True,
            'order_id': order.id if order else None
        })
    except Exception as e:
        print(f"Error fetching pending delivery: {e}")
        return jsonify({'success': False, 'order_id': None}), 500





# ==================== REPORT GENERATION FUNCTIONS ====================
def generate_sales_report(filters, format_type):
    """Generate sales report CSV"""
    try:
        query = Order.query
        if filters:
            query = query.filter(*filters)
        orders = query.order_by(Order.created_at.desc()).all()
        
        # Use StringIO for text data
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['Order ID', 'Date', 'Customer', 'Amount (UGX)', 'Status', 
                         'Payment Method', 'Items', 'Delivery Location'])
        
        for order in orders:
            writer.writerow([
                order.id,
                order.created_at.strftime('%Y-%m-%d %H:%M'),
                order.user.fullname if order.user else 'Guest',
                f"{order.total_amount:,.0f}",
                order.status.title() if order.status else 'Pending',
                order.payment_method or 'N/A',
                len(order.order_items),
                order.delivery_address or 'N/A'
            ])
        
        total_revenue = sum(o.total_amount for o in orders)
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Orders', len(orders)])
        writer.writerow(['Total Revenue', f'UGX {total_revenue:,.0f}'])
        writer.writerow(['Average Order Value', f'UGX {(total_revenue/len(orders)):,.0f}' if orders else 'UGX 0'])
        
        # Get the CSV content as string
        csv_content = output.getvalue()
        output.close()
        
        # Create response with proper headers
        from flask import make_response
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=sales_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error in sales report: {str(e)}")
        traceback.print_exc()
        flash(f'Error generating report: {str(e)}', 'danger')
        return redirect(url_for('admin_reports'))

def generate_users_report(filters, format_type):
    """Generate users report CSV"""
    try:
        users = User.query.order_by(User.created_at.desc()).all()
        
        # Use StringIO for text data
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['User ID', 'Name', 'Email', 'Type', 'Phone', 'Business', 
                         'Status', 'Joined Date'])
        
        for user in users:
            writer.writerow([
                user.id,
                user.fullname,
                user.email,
                user.user_type.title() if user.user_type else 'Buyer',
                user.phone or 'N/A',
                user.business_name or 'N/A',
                'Active' if user.is_active else 'Inactive',
                user.created_at.strftime('%Y-%m-%d') if user.created_at else 'N/A'
            ])
        
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Users', len(users)])
        writer.writerow(['Buyers', User.query.filter_by(user_type='buyer').count()])
        writer.writerow(['Sellers', User.query.filter_by(user_type='seller').count()])
        writer.writerow(['Staff', User.query.filter_by(user_type='staff').count()])
        writer.writerow(['Admins', User.query.filter_by(user_type='admin').count()])
        writer.writerow(['Active Users', User.query.filter_by(is_active=True).count()])
        writer.writerow(['Inactive Users', User.query.filter_by(is_active=False).count()])
        
        # Get the CSV content as string
        csv_content = output.getvalue()
        output.close()
        
        # Create response with proper headers
        from flask import make_response
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=users_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error in users report: {str(e)}")
        traceback.print_exc()
        flash(f'Error generating report: {str(e)}', 'danger')
        return redirect(url_for('admin_reports'))

def generate_products_report(filters, format_type):
    """Generate products report CSV"""
    try:
        products = Product.query.order_by(Product.created_at.desc()).all()
        
        # Use StringIO for text data
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['Product ID', 'Name', 'Seller', 'Category', 'Price (UGX)', 
                         'Stock', 'Sold', 'Status', 'Created Date'])
        
        for product in products:
            writer.writerow([
                product.id,
                product.name,
                product.seller.business_name or product.seller.fullname if product.seller else 'N/A',
                product.category.title() if product.category else 'General',
                f"{product.price:,.0f}",
                product.stock,
                product.sold_count or 0,
                'Active' if product.is_active else 'Inactive',
                product.created_at.strftime('%Y-%m-%d') if product.created_at else 'N/A'
            ])
        
        total_value = sum(p.price * p.stock for p in products)
        total_sold = sum(p.sold_count or 0 for p in products)
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Products', len(products)])
        writer.writerow(['Active Products', Product.query.filter_by(is_active=True).count()])
        writer.writerow(['Total Inventory Value', f'UGX {total_value:,.0f}'])
        writer.writerow(['Total Items Sold', total_sold])
        
        # Get the CSV content as string
        csv_content = output.getvalue()
        output.close()
        
        # Create response with proper headers
        from flask import make_response
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=products_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error in products report: {str(e)}")
        traceback.print_exc()
        flash(f'Error generating report: {str(e)}', 'danger')
        return redirect(url_for('admin_reports'))

def generate_sellers_report(filters, format_type):
    """Generate sellers report CSV"""
    try:
        sellers = User.query.filter_by(user_type='seller').order_by(User.created_at.desc()).all()
        
        # Use StringIO for text data
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['Seller ID', 'Name', 'Email', 'Business', 'Phone', 'NIN', 
                         'Products', 'Rating', 'Revenue', 'Status', 'Joined'])
        
        for seller in sellers:
            products_count = Product.query.filter_by(seller_id=seller.id).count()
            total_revenue = db.session.query(
                func.sum(OrderItem.price * OrderItem.quantity)
            ).join(Order).filter(
                OrderItem.seller_id == seller.id,
                Order.status == 'completed'
            ).scalar() or 0
            
            writer.writerow([
                seller.id,
                seller.fullname,
                seller.email,
                seller.business_name or 'N/A',
                seller.phone or 'N/A',
                seller.nin or 'N/A',
                products_count,
                f"{seller.seller_rating or 0:.1f}",
                f"{total_revenue:,.0f}",
                'Active' if seller.is_active else 'Inactive',
                seller.created_at.strftime('%Y-%m-%d') if seller.created_at else 'N/A'
            ])
        
        total_revenue_all = db.session.query(
            func.sum(OrderItem.price * OrderItem.quantity)
        ).join(Order).filter(
            Order.status == 'completed'
        ).scalar() or 0
        
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total Sellers', len(sellers)])
        writer.writerow(['Active Sellers', User.query.filter_by(user_type='seller', is_active=True).count()])
        writer.writerow(['Total Products Sold', db.session.query(func.sum(Product.sold_count)).scalar() or 0])
        writer.writerow(['Total Seller Revenue', f'UGX {total_revenue_all:,.0f}'])
        
        # Get the CSV content as string
        csv_content = output.getvalue()
        output.close()
        
        # Create response with proper headers
        from flask import make_response
        response = make_response(csv_content)
        response.headers['Content-Disposition'] = f'attachment; filename=sellers_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        print(f"Error in sellers report: {str(e)}")
        traceback.print_exc()
        flash(f'Error generating report: {str(e)}', 'danger')
        return redirect(url_for('admin_reports'))



# ==================== EMAIL FUNCTIONS ====================
def generate_verification_token():
    return secrets.token_urlsafe(32)

def send_verification_email(email, token):
    try:
        smtp_server = "smtp.gmail.com"
        port = 587
        sender_email = app.config['MAIL_USERNAME']
        password = app.config['MAIL_PASSWORD']
        
        verification_link = url_for('verify_email', token=token, _external=True)
        
        message = MIMEMultipart("alternative")
        message["Subject"] = "Verify Your Email for ShopMax"
        message["From"] = sender_email
        message["To"] = email
        
        html = f"""
        <html>
          <body>
            <h2>Welcome to ShopMax!</h2>
            <p>Please verify your email address to complete your registration.</p>
            <p><a href="{verification_link}" style="background-color: #4CAF50; color: white; padding: 14px 20px; text-align: center; text-decoration: none; display: inline-block; border-radius: 5px;">Verify Email</a></p>
            <p>Or copy this link: {verification_link}</p>
            <p>This link will expire in 1 hour.</p>
          </body>
        </html>
        """
        
        message.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(sender_email, password)
            server.send_message(message)
        
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def send_order_notifications(order, order_items):
    try:
        admin_users = User.query.filter_by(user_type='admin').all()
        seller_ids = set(item.seller_id for item in order_items)
        sellers = User.query.filter(User.id.in_(seller_ids)).all()
        
        print(f"📦 New Order #{order.id} placed by {order.user.fullname}")
        print(f"💰 Total: UGX {order.total_amount:,.0f}")
        print(f"👥 Notifying {len(admin_users)} admin(s) and {len(sellers)} seller(s)")
        
        for seller in sellers:
            seller_items = [item for item in order_items if item.seller_id == seller.id]
            total_seller_amount = sum(item.quantity * item.price for item in seller_items)
            print(f"   📧 Seller {seller.business_name or seller.fullname}: {len(seller_items)} items, Total: UGX {total_seller_amount:,.0f}")
            
    except Exception as e:
        print(f"Error sending notifications: {str(e)}")

def send_password_reset_email(email, token):
    try:
        smtp_server = "smtp.gmail.com"
        port = 587
        sender_email = app.config['MAIL_USERNAME']
        password = app.config['MAIL_PASSWORD']
        
        reset_link = url_for('reset_password', token=token, _external=True)
        
        message = MIMEMultipart("alternative")
        message["Subject"] = "Password Reset Request - ShopMax"
        message["From"] = sender_email
        message["To"] = email
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                .email-container {{
                    font-family: Arial, sans-serif;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                    border-radius: 10px 10px 0 0;
                }}
                .content {{
                    background: #f5f5f5;
                    padding: 30px;
                    border-radius: 0 0 10px 10px;
                }}
                .button {{
                    display: inline-block;
                    padding: 15px 30px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                    margin: 20px 0;
                }}
                .footer {{
                    text-align: center;
                    color: #666;
                    font-size: 12px;
                    margin-top: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="email-container">
                <div class="header">
                    <h1>ShopMax</h1>
                    <p>Password Reset Request</p>
                </div>
                <div class="content">
                    <h2>Hello!</h2>
                    <p>We received a request to reset your password for your ShopMax account.</p>
                    <p>Click the button below to reset your password:</p>
                    
                    <div style="text-align: center;">
                        <a href="{reset_link}" class="button">Reset Password</a>
                    </div>
                    
                    <p>Or copy and paste this link into your browser:</p>
                    <p style="word-break: break-all;">{reset_link}</p>
                    
                    <p><strong>This link will expire in 1 hour.</strong></p>
                    
                    <p>If you didn't request a password reset, please ignore this email or contact support if you have concerns.</p>
                    
                    <hr style="border: 1px solid #ddd; margin: 20px 0;">
                    
                    <p style="color: #666;">
                        <strong>For your security:</strong>
                        <br>• Never share this link with anyone
                        <br>• ShopMax staff will never ask for your password
                        <br>• Make sure you're on the official ShopMax website
                    </p>
                </div>
                <div class="footer">
                    <p>&copy; 2024 ShopMax. All rights reserved.</p>
                    <p>This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text = f"""
        Password Reset Request - ShopMax
        
        We received a request to reset your password for your ShopMax account.
        
        Click the link below to reset your password:
        {reset_link}
        
        This link will expire in 1 hour.
        
        If you didn't request a password reset, please ignore this email.
        
        For your security:
        • Never share this link with anyone
        • ShopMax staff will never ask for your password
        
        © 2024 ShopMax
        """
        
        message.attach(MIMEText(text, "plain"))
        message.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(sender_email, password)
            server.send_message(message)
        
        print(f"✅ Password reset email sent to {email}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending password reset email: {e}")
        return False

# ==================== INITIALIZATION FUNCTIONS ====================
def create_admin_user():
    with app.app_context():
        admin_user = User.query.filter_by(email='shopmax4321@gmail.com').first()
        if not admin_user:
            admin_user = User(
                fullname='ShopMax Admin',
                email='shopmax4321@gmail.com',
                phone='0000000000',
                location='Admin Office',
                password=generate_password_hash('ShopMax1234'),
                user_type='admin',
                is_active=True
            )
            db.session.add(admin_user)
            db.session.commit()
            print("✅ Admin user created successfully!")

def create_demo_nin_database():
    """Create demo NIN database for testing"""
    demo_nins = [
        {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15"},
        {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22"},
        {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30"},
        {"nin": "CF321654987GH", "full_name": "Mary Williams", "dob": "1996-03-18"},
        {"nin": "CM789123456IJ", "full_name": "David Brown", "dob": "1994-07-25"},
        {"nin": "CF147258369KL", "full_name": "Sarah Taylor", "dob": "1997-09-12"},
        {"nin": "CM369258147MN", "full_name": "Michael Anderson", "dob": "1993-12-05"},
        {"nin": "CF951753456OP", "full_name": "Elizabeth Thomas", "dob": "1999-01-28"},
        {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15"},
        {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22"},
        {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30"},
        
    ]
    
    for nin_data in demo_nins:
        if not NINVerification.query.filter_by(nin=nin_data["nin"]).first():
            nin_record = NINVerification(
                nin=nin_data["nin"],
                full_name=nin_data["full_name"],
                date_of_birth=nin_data["dob"]
            )
            db.session.add(nin_record)
    
    db.session.commit()
    print("✅ Demo NIN database created!")



def create_sample_products():
    try:
        admin = User.query.filter_by(email='shopmax4321@gmail.com').first()
        if not admin:
            print("❌ No admin user found for sample products")
            return
            
        sample_products = [
            {'name': 'Wireless Bluetooth Headphones', 'description': 'High-quality wireless headphones with noise cancellation', 'price': 25000.0, 'category': 'electronics', 'stock': 10, 'brand': 'AudioTech', 'condition': 'new'},
            {'name': 'Smart Watch Fitness Tracker', 'description': 'Track your fitness goals with this advanced smartwatch', 'price': 15000.0, 'category': 'electronics', 'stock': 15, 'brand': 'FitGadget', 'condition': 'new'},
            {'name': 'Cotton T-Shirt', 'description': 'Comfortable cotton t-shirt for everyday wear', 'price': 5000.0, 'category': 'fashion', 'stock': 50, 'brand': 'FashionWear', 'condition': 'new'}
        ]
        
        for product_data in sample_products:
            product = Product(
                name=product_data['name'],
                description=product_data['description'],
                price=product_data['price'],
                category=product_data['category'],
                stock=product_data['stock'],
                brand=product_data['brand'],
                condition=product_data['condition'],
                seller_id=admin.id,
                is_active=True
            )
            db.session.add(product)
        
        db.session.commit()
        print("✅ Sample products created successfully!")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error creating sample products: {e}")



def initialize_database():
    """Initialize database with all required data"""
    with app.app_context():
        try:
            # Create all tables
            db.create_all()
            print("✅ Database tables created successfully!")
            
            # Create initial data
            create_admin_user()
            
            # Create NIN database - ensure it runs
            print("📋 Creating demo NIN database...")
            # Clear existing NINs first
            NINVerification.query.delete()
            db.session.commit()
            
            # Add NINs
            demo_nins = [
                {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15"},
                {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22"},
                {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30"},
                {"nin": "CF321654987GH", "full_name": "Mary Williams", "dob": "1996-03-18"},
                {"nin": "CM789123456IJ", "full_name": "David Brown", "dob": "1994-07-25"},
                {"nin": "CF147258369KL", "full_name": "Sarah Taylor", "dob": "1997-09-12"},
                {"nin": "CM369258147MN", "full_name": "Michael Anderson", "dob": "1993-12-05"},
                {"nin": "CF951753456OP", "full_name": "Elizabeth Thomas", "dob": "1999-01-28"},
            ]
            
            nin_count = 0
            for nin_data in demo_nins:
                nin_record = NINVerification(
                    nin=nin_data["nin"],
                    full_name=nin_data["full_name"],
                    date_of_birth=nin_data["dob"],
                    is_valid=True
                )
                db.session.add(nin_record)
                nin_count += 1
            
            db.session.commit()
            print(f"✅ Added {nin_count} demo NINs")
            
            # Create UCU emails
            try:
                ucu_count = generate_complete_ucu_data()
                print(f"✅ Added {ucu_count} UCU emails")
            except Exception as e:
                print(f"⚠️ Warning: Could not add UCU emails: {e}")
            
            # Create sample products if none exist
            if Product.query.count() == 0:
                create_sample_products()
                
            print("✅ Database initialization complete!")
            print(f"📊 NINs in database: {NINVerification.query.count()}")
            
        except Exception as e:
            print(f"❌ Error initializing database: {e}")
            traceback.print_exc()

# ==================== CONTEXT PROCESSORS ====================
@app.context_processor
def inject_user():
    current_user = get_current_user()
    return dict(current_user=current_user, user=current_user)

@app.context_processor
def inject_cart_count():
    cart_count = 0
    if 'user_id' in session:
        user = get_current_user()
        if user and user.user_type == 'buyer':
            cart_count = Cart.query.filter_by(user_id=session['user_id']).count()
        else:
            cart_count = 0
    else:
        session_cart = session.get('cart', {})
        cart_count = sum(session_cart.values()) if session_cart else 0
    return dict(cart_count=cart_count)

@app.context_processor
def utility_processor():
    return dict(now=datetime.utcnow)





# ==================== UPDATED REGISTRATION ROUTES ====================

@app.route('/register', methods=['GET', 'POST'])
def unified_register():
    """Unified registration for UCU students, staff, and Google users"""
    
    if request.method == 'POST':
        auth_type = request.form.get('auth_type')
        
        if auth_type == 'ucu':
            email = request.form.get('email')
            user_type = request.form.get('user_type')  # This is 'buyer' or 'seller'
            account_type = request.form.get('account_type')  # This is 'student' or 'staff'
            
            if account_type == 'student':
                if not email or not email.endswith('@students.ucu.ac.ug'):
                    flash('❌ Students must use their UCU student email (e.g., B22564@students.ucu.ac.ug)', 'danger')
                    return render_template('register.html')
                
                student_part = email.split('@')[0]
                if not re.match(r'^[AB]\d{5}$', student_part):
                    flash('❌ Invalid student email format. Use A or B followed by 5 digits', 'danger')
                    return render_template('register.html')
                
                ucu_record = UCUEmail.query.filter_by(email=email, is_active=True).first()
                if not ucu_record:
                    flash('❌ This email is not recognized in the UCU system.', 'danger')
                    return render_template('register.html')
                
                if ucu_record.user_type != 'student':
                    flash('❌ This email belongs to UCU staff. Please select "Staff" option.', 'danger')
                    return render_template('register.html')
                
                if User.query.filter_by(email=email).first():
                    flash('❌ This email is already registered. Please login instead.', 'warning')
                    return render_template('register.html')
                
                session['verified_ucu_email'] = email
                session['verified_ucu_name'] = ucu_record.full_name
                session['verified_ucu_student'] = ucu_record.student_number
                session['verified_ucu_dept'] = ucu_record.department
                session['verified_ucu_type'] = 'student'
                
                if user_type == 'seller':
                    return redirect(url_for('complete_seller_registration'))
                else:
                    return redirect(url_for('complete_buyer_registration'))
            
            elif account_type == 'staff':
                if not email or not email.endswith('@ucu.ac.ug') or email.endswith('@students.ucu.ac.ug'):
                    flash('❌ Staff must use their UCU staff email (e.g., okello@ucu.ac.ug)', 'danger')
                    return render_template('register.html')
                
                ucu_record = UCUEmail.query.filter_by(email=email, is_active=True).first()
                if not ucu_record:
                    flash('❌ This email is not recognized in the UCU system.', 'danger')
                    return render_template('register.html')
                
                if ucu_record.user_type != 'staff':
                    flash('❌ This email belongs to a student. Please select "Student" option.', 'danger')
                    return render_template('register.html')
                
                if User.query.filter_by(email=email).first():
                    flash('❌ This email is already registered. Please login instead.', 'warning')
                    return render_template('register.html')
                
                session['verified_ucu_email'] = email
                session['verified_ucu_name'] = ucu_record.full_name
                session['verified_ucu_dept'] = ucu_record.department
                session['verified_ucu_title'] = ucu_record.staff_title
                session['verified_ucu_type'] = 'staff'
                
                if user_type == 'seller':
                    return redirect(url_for('complete_seller_registration'))
                else:
                    return redirect(url_for('complete_buyer_registration'))
        
        elif auth_type == 'google':
            session['google_registration_type'] = request.form.get('google_user_type', 'buyer')
            return redirect(url_for('google_login'))
    
    return render_template('register.html')


@app.route('/complete-buyer-registration', methods=['GET', 'POST'])
def complete_buyer_registration():
    """Complete registration for UCU students/staff as buyers"""
    if 'verified_ucu_email' not in session:
        flash('Please verify your UCU email first.', 'warning')
        return redirect(url_for('unified_register'))
    
    email = session['verified_ucu_email']
    ucu_name = session['verified_ucu_name']
    ucu_type = session.get('verified_ucu_type', 'student')
    student_number = session.get('verified_ucu_student', '')
    department = session.get('verified_ucu_dept', '')
    staff_title = session.get('verified_ucu_title', '')
    
    if request.method == 'POST':
        phone = request.form.get('phone')
        location = request.form.get('location')
        delivery_address = request.form.get('delivery_address')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('complete_buyer_registration.html', 
                                 email=email, name=ucu_name, 
                                 student_number=student_number, department=department,
                                 ucu_type=ucu_type, staff_title=staff_title)
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('complete_buyer_registration.html', 
                                 email=email, name=ucu_name, 
                                 student_number=student_number, department=department,
                                 ucu_type=ucu_type, staff_title=staff_title)
        
        if not phone or not re.match(r'^[0-9]{10}$', phone):
            flash('Please enter a valid 10-digit phone number.', 'danger')
            return render_template('complete_buyer_registration.html', 
                                 email=email, name=ucu_name, 
                                 student_number=student_number, department=department,
                                 ucu_type=ucu_type, staff_title=staff_title)
        
        try:
            new_user = User(
                fullname=ucu_name,
                email=email,
                phone=phone,
                location=location,
                delivery_address=delivery_address,
                password=generate_password_hash(password),
                user_type='buyer',  # Always set as buyer
                is_active=True
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # Clear session
            session.pop('verified_ucu_email', None)
            session.pop('verified_ucu_name', None)
            session.pop('verified_ucu_student', None)
            session.pop('verified_ucu_dept', None)
            session.pop('verified_ucu_type', None)
            session.pop('verified_ucu_title', None)
            
            session['user_id'] = new_user.id
            session['user_name'] = new_user.fullname
            session['user_type'] = new_user.user_type
            
            merge_session_cart_with_user(new_user.id)
            
            flash(f'✅ Welcome to ShopMax, {ucu_name}! Your UCU account has been verified.', 'success')
            return redirect(url_for('products'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating buyer account: {e}")
            traceback.print_exc()
            flash('Error creating account. Please try again.', 'danger')
    
    return render_template('complete_buyer_registration.html', 
                         email=email, 
                         name=ucu_name, 
                         student_number=student_number,
                         department=department,
                         ucu_type=ucu_type,
                         staff_title=staff_title)




# Remove the separate staff registration route since it's now handled above
# You can delete the register_staff route or keep it for backward compatibility
@app.route('/register/staff', methods=['GET', 'POST'])
def register_staff():
    """Legacy staff registration - redirects to unified register"""
    flash('Please use the unified registration form.', 'info')
    return redirect(url_for('unified_register'))


@app.route('/debug/check-user-subscription')
@login_required
def debug_check_user_subscription():
    """Check if user has subscription tier set correctly"""
    user = get_current_user()
    
    html = f"""
    <h1>User Subscription Check</h1>
    <p><strong>User ID:</strong> {user.id}</p>
    <p><strong>User Type:</strong> {user.user_type}</p>
    <p><strong>Subscription Tier:</strong> <code>{user.subscription_tier}</code></p>
    <p><strong>Subscription Expiry:</strong> {user.subscription_expiry}</p>
    <p><strong>Is None?</strong> {user.subscription_tier is None}</p>
    
    <h3>Plan Eligibility:</h3>
    <p>Starter Plan (Free): <strong>{'✅ Available' if user.subscription_tier is None else '❌ Not Available'}</strong></p>
    <p>Business Plan: <strong>✅ Always Available</strong></p>
    <p>Semester Plan: <strong>✅ Always Available</strong></p>
    
    <p><a href="/seller/subscription">Go to Subscription Page</a></p>
    """
    
    return html




# ==================== FIXED LOGIN ROUTE ====================
@app.route('/login', methods=['GET', 'POST'])
def login():
    # If user is already logged in, redirect to appropriate dashboard
    if 'user_id' in session:
        user = get_current_user()
        if user:
            if user.user_type == 'seller':
                return redirect(url_for('dashboard'))
            elif user.user_type == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('products'))
    
    if request.method == 'POST':
        # Check if it's an AJAX request
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        print(f"🔐 Login attempt - Email: {email}")
        
        # Basic validation
        if not email or not password:
            if is_ajax:
                return jsonify({'success': False, 'message': 'Please provide both email and password.'})
            else:
                flash('Please provide both email and password.', 'danger')
                return render_template('login.html')
        
        # Simple email format validation
        if '@' not in email or '.' not in email.split('@')[-1]:
            if is_ajax:
                return jsonify({'success': False, 'message': 'Please enter a valid email address.'})
            else:
                flash('Please enter a valid email address.', 'danger')
                return render_template('login.html')
        
        # Find user by email (case-insensitive)
        user = User.query.filter(func.lower(User.email) == email).first()
        
        if user:
            print(f"✅ User found: {user.email} (Type: {user.user_type})")
            print(f"   Has password: {bool(user.password)}")
            
            # Check if user has a password
            if not user.password:
                print("⚠️ User has no password - likely a Google OAuth user")
                if is_ajax:
                    return jsonify({
                        'success': False,
                        'message': 'This account was created with Google. Please use "Continue with Google" to login.'
                    })
                else:
                    flash('This account was created with Google. Please use "Continue with Google" to login.', 'info')
                    return render_template('login.html')
            
            # Verify password
            if check_password_hash(user.password, password):
                print("✅ Password correct")
                
                # Check if account is active
                if not user.is_active:
                    if is_ajax:
                        return jsonify({
                            'success': False,
                            'message': 'Your account has been deactivated. Please contact support.'
                        })
                    else:
                        flash('Your account has been deactivated. Please contact support.', 'danger')
                        return render_template('login.html')
                
                # Login successful
                session['user_id'] = user.id
                session['user_name'] = user.fullname
                session['user_type'] = user.user_type
                
                # Set session expiry if remember me is checked
                if remember:
                    session.permanent = True
                    app.permanent_session_lifetime = timedelta(days=30)
                
                # Merge cart if buyer
                if user.user_type == 'buyer':
                    merge_session_cart_with_user(user.id)
                
                # Determine redirect URL
                if user.user_type == 'seller':
                    if not has_active_subscription(user):
                        redirect_url = url_for('seller_subscription')
                    else:
                        redirect_url = url_for('dashboard')
                elif user.user_type == 'admin':
                    redirect_url = url_for('admin_dashboard')
                elif user.user_type == 'staff':
                    redirect_url = url_for('products')
                else:
                    redirect_url = url_for('products')
                
                if is_ajax:
                    return jsonify({
                        'success': True,
                        'message': f'Welcome back, {user.fullname}!',
                        'redirect': redirect_url
                    })
                else:
                    flash(f'Welcome back, {user.fullname}!', 'success')
                    return redirect(redirect_url)
            else:
                print("❌ Password incorrect")
        else:
            print(f"❌ User not found: {email}")
        
        # Login failed
        if is_ajax:
            return jsonify({
                'success': False,
                'message': 'Invalid email or password. Please check your credentials and try again.'
            })
        else:
            flash('Invalid email or password. Please try again.', 'danger')
            return render_template('login.html')
    
    # GET request - show login page
    return render_template('login.html')


@app.route('/api/admin/delivery/performance')
@admin_required
def delivery_performance_analytics():
    """Get delivery performance analytics"""
    try:
        period = request.args.get('period', 'week')
        
        # Calculate date range
        end_date = datetime.utcnow()
        if period == 'day':
            start_date = end_date - timedelta(days=1)
        elif period == 'week':
            start_date = end_date - timedelta(days=7)
        elif period == 'month':
            start_date = end_date - timedelta(days=30)
        else:
            start_date = end_date - timedelta(days=7)
        
        # Get completed deliveries in period
        completed_deliveries = Order.query.filter(
            Order.status == 'completed',
            Order.actual_delivery >= start_date,
            Order.actual_delivery <= end_date
        ).all()
        
        # Calculate average delivery time using created_at instead of assigned_at
        total_time = 0
        count = 0
        for delivery in completed_deliveries:
            if delivery.created_at and delivery.actual_delivery:
                time_taken = (delivery.actual_delivery - delivery.created_at).total_seconds() / 60
                total_time += time_taken
                count += 1
        
        avg_delivery_time = total_time / count if count > 0 else 0
        
        # Get rider performance
        riders = DeliveryPerson.query.all()
        rider_performance = []
        for rider in riders:
            rider_deliveries = Order.query.filter_by(
                delivery_person_id=rider.id,
                status='completed'
            ).count()
            
            rider_performance.append({
                'name': rider.name,
                'deliveries': rider_deliveries,
                'avg_time': 0  # Calculate if needed
            })
        
        return jsonify({
            'success': True,
            'avg_delivery_time': round(avg_delivery_time, 1),
            'total_completed': len(completed_deliveries),
            'rider_performance': rider_performance
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/delivery/<int:delivery_id>/update-location', methods=['POST'])
@login_required
def update_delivery_location(delivery_id):
    """API for riders to update their location (called from mobile)"""
    try:
        data = request.get_json()
        delivery = Order.query.get_or_404(delivery_id)
        
        # Verify that the rider is the one assigned
        if delivery.delivery_person_id != data.get('rider_id'):
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Update rider's current location
        rider = DeliveryPerson.query.get(delivery.delivery_person_id)
        if rider:
            rider.current_location = data.get('location_address')
        
        # Create tracking point
        tracking = DeliveryTracking(
            order_id=delivery_id,
            delivery_person_id=rider.id,
            status=delivery.status,
            location_lat=data.get('lat'),
            location_lng=data.get('lng'),
            location_address=data.get('location_address'),
            speed=data.get('speed'),
            battery_level=data.get('battery'),
            notes=data.get('notes')
        )
        db.session.add(tracking)
        
        # Update order's current location
        delivery.current_location = data.get('location_address')
        delivery.last_updated = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/delivery/<int:delivery_id>/upload-proof', methods=['POST'])
@login_required
def upload_delivery_proof(delivery_id):
    """API for riders to upload delivery proof"""
    try:
        delivery = Order.query.get_or_404(delivery_id)
        
        # Verify rider
        rider_id = request.form.get('rider_id')
        if delivery.delivery_person_id != int(rider_id):
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Handle photo upload
        photo_filename = None
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
                photo_filename = f"delivery_proof_{delivery_id}_{timestamp}{filename}"
                ensure_upload_folder()
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
        
        # Create delivery proof
        proof = DeliveryProof(
            order_id=delivery_id,
            delivery_person_id=rider_id,
            photo=photo_filename,
            signature=request.form.get('signature'),
            recipient_name=request.form.get('recipient_name'),
            recipient_phone=request.form.get('recipient_phone'),
            notes=request.form.get('notes')
        )
        db.session.add(proof)
        
        # Update order status
        delivery.status = 'delivered'
        delivery.actual_delivery = datetime.utcnow()
        
        # Create tracking update
        tracking = DeliveryTracking(
            order_id=delivery_id,
            delivery_person_id=rider_id,
            status='delivered',
            location_address=delivery.current_location,
            notes='Delivery completed with proof'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/delivery/<int:delivery_id>/timeline')
@admin_required
def get_delivery_timeline(delivery_id):
    """Get delivery timeline for analytics"""
    try:
        delivery = Order.query.get_or_404(delivery_id)
        tracking_points = DeliveryTracking.query.filter_by(order_id=delivery_id).order_by(DeliveryTracking.created_at).all()
        
        timeline = []
        for point in tracking_points:
            timeline.append({
                'time': point.created_at.strftime('%H:%M'),
                'status': point.status,
                'location': point.location_address,
                'speed': point.speed
            })
        
        # Calculate performance metrics
        metrics = {
            'total_time': None,
            'waiting_time': None,
            'travel_time': None
        }
        
        if len(tracking_points) >= 2:
            first = tracking_points[0].created_at
            last = tracking_points[-1].created_at
            metrics['total_time'] = (last - first).total_seconds() / 60
        
        return jsonify({
            'success': True,
            'timeline': timeline,
            'metrics': metrics
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/boda/tracking/<int:boda_id>')
@login_required
def boda_tracking_portal(boda_id):
    """Portal for boda riders to update their deliveries"""
    boda = DeliveryPerson.query.get_or_404(boda_id)
    
    # Get active assignments
    active_assignments = DeliveryAssignment.query.filter_by(
        delivery_person_id=boda_id,
        status='assigned'
    ).all()
    
    # Get today's completed deliveries
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = Order.query.filter(
        Order.delivery_person_id == boda_id,
        Order.status == 'delivered',
        Order.actual_delivery >= today_start
    ).count()
    
    return render_template('boda_tracking_portal.html',
                         boda=boda,
                         active_assignments=active_assignments,
                         today_completed=today_completed,
                         now=datetime.utcnow)

@app.route('/boda/api/start-delivery/<int:assignment_id>', methods=['POST'])
@login_required
def start_delivery(assignment_id):
    """API for boda rider to start a delivery"""
    try:
        assignment = DeliveryAssignment.query.get_or_404(assignment_id)
        boda_id = request.json.get('boda_id')
        
        if assignment.delivery_person_id != boda_id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        assignment.status = 'picked_up'
        assignment.order.status = 'out_for_delivery'
        
        # Create tracking point
        tracking = DeliveryTracking(
            order_id=assignment.order_id,
            delivery_person_id=boda_id,
            status='picked_up',
            location_address=request.json.get('location'),
            notes='Delivery started'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/boda/api/mark-nearby/<int:assignment_id>', methods=['POST'])
@login_required
def mark_nearby(assignment_id):
    """API for boda rider to mark that they're near destination"""
    try:
        assignment = DeliveryAssignment.query.get_or_404(assignment_id)
        boda_id = request.json.get('boda_id')
        
        if assignment.delivery_person_id != boda_id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Create tracking point
        tracking = DeliveryTracking(
            order_id=assignment.order_id,
            delivery_person_id=boda_id,
            status='near_destination',
            location_address=request.json.get('location'),
            notes='Near delivery location'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        # Send notification to customer (implement your notification system)
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500





# ==================== API ROUTES ====================
@app.route('/api/cart/count')
def get_cart_count():
    """Get current cart count"""
    try:
        if 'user_id' in session:
            user = get_current_user()
            if user and user.user_type == 'buyer':
                count = Cart.query.filter_by(user_id=session['user_id']).count()
            else:
                count = 0
        else:
            session_cart = session.get('cart', {})
            count = sum(session_cart.values())
        
        return jsonify({'count': count})
    except Exception as e:
        print(f"Error getting cart count: {e}")
        return jsonify({'count': 0})

@app.route('/api/cart/add', methods=['POST'])
def add_to_cart():
    data = request.get_json()
    product_id = data.get('product_id')
    quantity = data.get('quantity', 1)
    
    try:
        product = Product.query.filter_by(id=product_id, is_active=True).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'})
        
        if product.stock < quantity:
            return jsonify({
                'success': False, 
                'message': f'Only {product.stock} items available in stock'
            })
        
        if 'user_id' in session:
            user = get_current_user()
            
            if user.user_type != 'buyer':
                return jsonify({'success': False, 'message': 'Only buyers can add items to cart.'})
            
            cart_item = Cart.query.filter_by(user_id=user.id, product_id=product_id).first()
            if cart_item:
                new_quantity = cart_item.quantity + quantity
                if new_quantity > product.stock:
                    return jsonify({
                        'success': False, 
                        'message': f'Cannot add more than available stock. You have {cart_item.quantity} in cart, only {product.stock - cart_item.quantity} more available.'
                    })
                cart_item.quantity = new_quantity
            else:
                cart_item = Cart(user_id=user.id, product_id=product_id, quantity=quantity)
                db.session.add(cart_item)
            
            db.session.commit()
            cart_count = Cart.query.filter_by(user_id=user.id).count()
        else:
            # For guest users - store in session
            cart = session.get('cart', {})
            if not cart:
                cart = {}
            
            product_key = str(product_id)
            current_qty = cart.get(product_key, 0)
            new_qty = current_qty + quantity
            
            if new_qty > product.stock:
                return jsonify({
                    'success': False,
                    'message': f'Cannot add more than available stock. You have {current_qty} in cart, only {product.stock - current_qty} more available.'
                })
            
            cart[product_key] = new_qty
            session['cart'] = cart
            session.modified = True
            cart_count = sum(cart.values())
        
        return jsonify({
            'success': True, 
            'message': 'Added to cart successfully',
            'cart_count': cart_count
        })
    
    except Exception as e:
        if 'user_id' in session:
            db.session.rollback()
        print(f"Error adding to cart: {e}")
        return jsonify({'success': False, 'message': 'Error adding to cart'})

@app.route('/api/cart/update', methods=['POST'])
def update_cart_item():
    data = request.get_json()
    cart_item_id = data.get('cart_item_id')
    quantity = data.get('quantity')
    
    if 'user_id' in session:
        user = get_current_user()
        
        if user.user_type != 'buyer':
            return jsonify({'success': False, 'message': 'Cart is only available for buyers.'})
        
        try:
            cart_item = Cart.query.filter_by(id=cart_item_id, user_id=user.id).first()
            
            if not cart_item:
                return jsonify({'success': False, 'message': 'Cart item not found'})
            
            if quantity <= 0:
                db.session.delete(cart_item)
            else:
                product = Product.query.get(cart_item.product_id)
                if quantity > product.stock:
                    return jsonify({
                        'success': False, 
                        'message': f'Only {product.stock} items available in stock'
                    })
                cart_item.quantity = quantity
            
            db.session.commit()
            
            cart_count = Cart.query.filter_by(user_id=user.id).count()
            cart_items = Cart.query.filter_by(user_id=user.id).all()
            subtotal = sum(item.quantity * item.product.price for item in cart_items)
            
            return jsonify({
                'success': True, 
                'message': 'Cart updated successfully',
                'cart_count': cart_count,
                'subtotal': subtotal
            })
        
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': 'Error updating cart'})
    else:
        try:
            cart = session.get('cart', {})
            
            if cart_item_id.startswith('session_'):
                product_id = cart_item_id.replace('session_', '')
                
                if quantity <= 0:
                    cart.pop(product_id, None)
                else:
                    product = Product.query.get(product_id)
                    if not product:
                        return jsonify({'success': False, 'message': 'Product not found'})
                    
                    if quantity > product.stock:
                        return jsonify({
                            'success': False, 
                            'message': f'Only {product.stock} items available in stock'
                        })
                    
                    cart[product_id] = quantity
                
                session['cart'] = cart
                
                cart_count = sum(cart.values())
                subtotal = 0
                for pid, qty in cart.items():
                    product = Product.query.get(pid)
                    if product:
                        subtotal += product.price * qty
                
                return jsonify({
                    'success': True, 
                    'message': 'Cart updated successfully',
                    'cart_count': cart_count,
                    'subtotal': subtotal
                })
            else:
                return jsonify({'success': False, 'message': 'Invalid cart item'})
        
        except Exception as e:
            return jsonify({'success': False, 'message': 'Error updating cart'})

@app.route('/api/cart/remove', methods=['POST'])
def remove_cart_item():
    data = request.get_json()
    cart_item_id = data.get('cart_item_id')
    
    if 'user_id' in session:
        user = get_current_user()
        
        if user.user_type != 'buyer':
            return jsonify({'success': False, 'message': 'Cart is only available for buyers.'})
        
        try:
            cart_item = Cart.query.filter_by(id=cart_item_id, user_id=user.id).first()
            
            if cart_item:
                db.session.delete(cart_item)
                db.session.commit()
                
                cart_count = Cart.query.filter_by(user_id=user.id).count()
                
                return jsonify({
                    'success': True, 
                    'message': 'Item removed from cart',
                    'cart_count': cart_count
                })
            else:
                return jsonify({'success': False, 'message': 'Cart item not found'})
        
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': 'Error removing item from cart'})
    else:
        try:
            cart = session.get('cart', {})
            
            if cart_item_id.startswith('session_'):
                product_id = cart_item_id.replace('session_', '')
                cart.pop(product_id, None)
                session['cart'] = cart
                
                cart_count = sum(cart.values())
                
                return jsonify({
                    'success': True, 
                    'message': 'Item removed from cart',
                    'cart_count': cart_count
                })
            else:
                return jsonify({'success': False, 'message': 'Invalid cart item'})
        
        except Exception as e:
            return jsonify({'success': False, 'message': 'Error removing item from cart'})

@app.route('/api/cart/clear', methods=['POST'])
def clear_cart():
    if 'user_id' in session:
        user = get_current_user()
        
        if user.user_type != 'buyer':
            return jsonify({'success': False, 'message': 'Cart is only available for buyers.'})
        
        try:
            Cart.query.filter_by(user_id=user.id).delete()
            db.session.commit()
            
            return jsonify({'success': True, 'message': 'Cart cleared successfully'})
        
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'message': 'Error clearing cart'})
    else:
        try:
            session.pop('cart', None)
            return jsonify({'success': True, 'message': 'Cart cleared successfully'})
        except Exception as e:
            return jsonify({'success': False, 'message': 'Error clearing cart'})

@app.route('/api/wishlist/toggle', methods=['POST'])
@login_required
def toggle_wishlist():
    user = get_current_user()
    data = request.get_json()
    product_id = data.get('product_id')
    
    try:
        product = Product.query.filter_by(id=product_id, is_active=True).first()
        if not product:
            return jsonify({'success': False, 'message': 'Product not found'})
        
        existing = Wishlist.query.filter_by(user_id=user.id, product_id=product_id).first()
        
        if existing:
            db.session.delete(existing)
            action = 'removed'
            in_wishlist = False
        else:
            wishlist_item = Wishlist(user_id=user.id, product_id=product_id)
            db.session.add(wishlist_item)
            action = 'added'
            in_wishlist = True
        
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Product {action} from wishlist',
            'in_wishlist': in_wishlist,
            'action': action
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error toggling wishlist: {e}")
        return jsonify({'success': False, 'message': 'Error updating wishlist'})

@app.route('/api/wishlist/remove', methods=['POST'])
@login_required
def remove_from_wishlist():
    user = get_current_user()
    data = request.get_json()
    product_id = data.get('product_id')
    
    try:
        wishlist_item = Wishlist.query.filter_by(user_id=user.id, product_id=product_id).first()
        if wishlist_item:
            db.session.delete(wishlist_item)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Removed from wishlist'})
        else:
            return jsonify({'success': False, 'message': 'Item not found in wishlist'})
    
    except Exception as e:
        db.session.rollback()
        print(f"Error removing from wishlist: {e}")
        return jsonify({'success': False, 'message': 'Error removing from wishlist'})

@app.route('/api/wishlist/clear', methods=['POST'])
@login_required
def clear_wishlist():
    user = get_current_user()
    
    try:
        Wishlist.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Wishlist cleared successfully'})
    
    except Exception as e:
        db.session.rollback()
        print(f"Error clearing wishlist: {e}")
        return jsonify({'success': False, 'message': 'Error clearing wishlist'})

@app.route('/api/wishlist/status')
@login_required
def get_wishlist_status():
    user = get_current_user()
    product_ids = request.args.getlist('product_ids[]')
    
    try:
        wishlist_items = Wishlist.query.filter_by(user_id=user.id).filter(
            Wishlist.product_id.in_(product_ids)
        ).all()
        
        wishlist_ids = [item.product_id for item in wishlist_items]
        
        return jsonify({
            'success': True,
            'wishlist_ids': wishlist_ids
        })
        
    except Exception as e:
        print(f"Error getting wishlist status: {e}")
        return jsonify({'success': False, 'wishlist_ids': []})

@app.route('/api/tracking/<int:order_id>/updates')
@login_required
def get_tracking_updates(order_id):
    order = Order.query.get_or_404(order_id)
    user = get_current_user()
    
    if user.user_type == 'buyer' and order.user_id != user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    updates = DeliveryTracking.query.filter_by(order_id=order_id).order_by(DeliveryTracking.created_at).all()
    
    updates_data = []
    for update in updates:
        updates_data.append({
            'id': update.id,
            'status': update.status,
            'location': update.location,
            'description': update.description,
            'created_at': update.created_at.strftime('%b %d, %Y • %I:%M %p')
        })
    
    current_location = None
    if order.delivery_person and order.delivery_person.current_location:
        current_location = order.delivery_person.current_location
    
    return jsonify({
        'status': order.delivery_status,
        'updates': updates_data,
        'current_location': current_location,
        'estimated_delivery': order.estimated_delivery.strftime('%I:%M %p') if order.estimated_delivery else None,
        'last_updated': order.last_updated.strftime('%I:%M %p') if order.last_updated else None
    })

# ==================== GOOGLE OAUTH ROUTES ====================
@app.route('/google/login')
def google_login():
    """Redirect to Google for authentication"""
    try:
        # Store the next URL if any
        session['next_url'] = request.referrer or url_for('unified_register')
        
        # Generate nonce for security
        nonce = secrets.token_urlsafe(16)
        session['google_nonce'] = nonce
        
        # Redirect to Google for authentication
        redirect_uri = url_for('google_callback', _external=True)
        return google.authorize_redirect(redirect_uri, nonce=nonce)
    
    except Exception as e:
        print(f"❌ Google login error: {e}")
        traceback.print_exc()
        flash('Failed to connect to Google. Please try again.', 'danger')
        return redirect(url_for('unified_register'))




@app.route('/complete/external', methods=['GET', 'POST'])
def complete_external_registration():
    """Complete registration for external users (non-UCU) as sellers"""
    # Check if we have the necessary session data
    if 'google_email' not in session:
        flash('Please authenticate with Google first.', 'warning')
        return redirect(url_for('unified_register'))
    
    email = session.get('google_email')
    name = session.get('google_name')
    
    if request.method == 'POST':
        phone = request.form.get('phone')
        business_name = request.form.get('business_name')
        nin = request.form.get('nin')
        location = request.form.get('location')
        business_address = request.form.get('business_address')
        password_input = request.form.get('password')
        
        # Validate phone
        if not phone or not re.match(r'^[0-9]{10}$', phone):
            flash('Please enter a valid 10-digit phone number.', 'danger')
            return render_template('complete_external.html', email=email, name=name)
        
        # Validate NIN
        if not nin:
            flash('NIN is required for seller accounts.', 'danger')
            return render_template('complete_external.html', email=email, name=name)
        
        # Verify NIN in demo database
        nin_record = NINVerification.query.filter_by(nin=nin, is_valid=True).first()
        if not nin_record:
            flash('❌ Invalid NIN. Please use one of the demo NINs provided.', 'danger')
            return render_template('complete_external.html', email=email, name=name)
        
        try:
            # If password provided, use it; otherwise generate random
            if password_input and len(password_input) >= 6:
                password_to_use = password_input
            else:
                password_to_use = secrets.token_urlsafe(12)
            
            hashed_password = generate_password_hash(password_to_use)
            
            new_user = User(
                fullname=name,
                email=email,
                phone=phone,
                location=location,
                business_name=business_name,
                business_address=business_address or location,
                nin=nin,
                password=hashed_password,
                user_type='seller',
                subscription_tier='basic',
                is_active=True
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # Store password in session to show once
            session['temp_password'] = password_to_use
            
            # Clear session data
            session.pop('google_email', None)
            session.pop('google_name', None)
            session.pop('google_picture', None)
            session.pop('google_registration_type', None)
            session.pop('google_nonce', None)
            
            # Log the user in
            session['user_id'] = new_user.id
            session['user_name'] = new_user.fullname
            session['user_type'] = new_user.user_type
            
            if password_input and len(password_input) >= 6:
                flash(f'✅ Welcome to ShopMax, {new_user.fullname}! Your seller account has been created.', 'success')
            else:
                flash(f'✅ Welcome to ShopMax, {new_user.fullname}! Your seller account has been created.', 'success')
                flash(f'🔑 Your temporary password is: {password_to_use}. Please change it in your profile.', 'info')
            
            return redirect(url_for('seller_subscription'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating external seller: {e}")
            traceback.print_exc()
            flash('Error creating account. Please try again.', 'danger')
    
    return render_template('complete_external.html',
                         email=email,
                         name=name)




@app.route('/fix-google-users')
def fix_google_users():
    """Fix existing Google users by removing invalid image URLs"""
    users = User.query.all()
    fixed_count = 0
    
    for user in users:
        if user.profile_image and user.profile_image.startswith(('http://', 'https://')):
            user.profile_image = None
            fixed_count += 1
            print(f"Fixed Google user: {user.email}")
    
    if fixed_count > 0:
        db.session.commit()
        return f"✅ Fixed {fixed_count} Google users by removing invalid image URLs"
    else:
        return "No Google users with invalid images found"




def create_demo_nin_database():
    """Create demo NIN database for testing"""
    demo_nins = [
        {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15"},
        {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22"},
        {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30"},
        {"nin": "CF321654987GH", "full_name": "Mary Williams", "dob": "1996-03-18"},
        {"nin": "CM789123456IJ", "full_name": "David Brown", "dob": "1994-07-25"},
        {"nin": "CF147258369KL", "full_name": "Sarah Taylor", "dob": "1997-09-12"},
        {"nin": "CM369258147MN", "full_name": "Michael Anderson", "dob": "1993-12-05"},
        {"nin": "CF951753456OP", "full_name": "Elizabeth Thomas", "dob": "1999-01-28"},
        {"nin": "CM159753248QR", "full_name": "James Wilson", "dob": "1991-04-10"},
        {"nin": "CF753951826ST", "full_name": "Patricia Moore", "dob": "1995-08-15"},
    ]
    
    count = 0
    for nin_data in demo_nins:
        # Check if NIN already exists
        existing = NINVerification.query.filter_by(nin=nin_data["nin"]).first()
        if not existing:
            nin_record = NINVerification(
                nin=nin_data["nin"],
                full_name=nin_data["full_name"],
                date_of_birth=nin_data["dob"],
                is_valid=True
            )
            db.session.add(nin_record)
            count += 1
        else:
            print(f"⏭️ NIN already exists: {nin_data['nin']}")
    
    if count > 0:
        db.session.commit()
        print(f"✅ Added {count} new demo NINs to database")
    else:
        print("✅ Demo NIN database already populated")



@app.route('/debug-nin-database')
def debug_nin_database():
    """Comprehensive debug for NIN database"""
    html = "<html><head><title>NIN Database Debug</title></head><body>"
    html += "<h1>🔍 NIN Database Debug</h1>"
    
    try:
        # Check if table exists
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        html += f"<h3>Tables in database: {tables}</h3>"
        
        if 'nin_verifications' not in tables:
            html += "<p style='color:red'>❌ NIN_VERIFICATIONS table does not exist!</p>"
            html += "<p><a href='/create-nin-table'>Create NIN Table</a></p>"
            return html
        
        # Count records
        count = NINVerification.query.count()
        html += f"<h3>Total NIN records: {count}</h3>"
        
        if count == 0:
            html += "<p style='color:red'>⚠️ No NIN records found! The table is empty.</p>"
            html += "<p><a href='/force-populate-nins'>Force Populate NINs</a></p>"
        else:
            # Show all NINs
            nins = NINVerification.query.all()
            html += "<h3>All NINs in database:</h3>"
            html += "<table border='1' cellpadding='8'>"
            html += "<tr><th>ID</th><th>NIN</th><th>Full Name</th><th>Valid</th><th>Created</th></tr>"
            for nin in nins:
                html += f"<tr>"
                html += f"<td>{nin.id}</td>"
                html += f"<td><strong>{nin.nin}</strong></td>"
                html += f"<td>{nin.full_name}</td>"
                html += f"<td>{'✅' if nin.is_valid else '❌'}</td>"
                html += f"<td>{nin.created_at}</td>"
                html += f"</tr>"
            html += "</table>"
            
            # Test specific NINs
            test_nins = ['CM123456789AB', 'CF987654321CD', 'CM456789123EF']
            html += "<h3>Test specific NINs:</h3><ul>"
            for test_nin in test_nins:
                record = NINVerification.query.filter_by(nin=test_nin).first()
                if record:
                    html += f"<li style='color:green'>✅ {test_nin} - FOUND</li>"
                else:
                    html += f"<li style='color:red'>❌ {test_nin} - NOT FOUND</li>"
            html += "</ul>"
        
        html += "<p><a href='/force-populate-nins'>Force Populate NINs</a> | <a href='/register'>Back to Register</a></p>"
        
    except Exception as e:
        html += f"<p style='color:red'>Error: {str(e)}</p>"
        import traceback
        html += f"<pre>{traceback.format_exc()}</pre>"
    
    html += "</body></html>"
    return html


@app.route('/create-test-rider')
def create_test_rider():
    """Create a test rider for debugging"""
    rider = DeliveryPerson(
        name='Test Boda Rider',
        phone='0700123456',
        vehicle_type='motorcycle',
        vehicle_number='UBA 123A',
        is_active=True
    )
    db.session.add(rider)
    db.session.commit()
    
    # Create a test order for this rider
    buyer = User.query.filter_by(user_type='buyer').first()
    if buyer:
        order = Order(
            user_id=buyer.id,
            total_amount=25000,
            status='out_for_delivery',
            delivery_address='UCU Main Gate, Mukono',
            delivery_person_id=rider.id
        )
        db.session.add(order)
        db.session.commit()
        
        return f'''
        <h1>✅ Test Rider Created!</h1>
        <p>Rider ID: {rider.id}</p>
        <p>Order ID: {order.id}</p>
        <p><strong>Rider Portal:</strong> <a href="/rider/tracking/{rider.id}">Open Rider App</a></p>
        <p><strong>Customer Tracking:</strong> <a href="/track-order/{order.id}">Track Order</a></p>
        '''
    
    return f'Rider created with ID: {rider.id}'




@app.route('/force-populate-nins')
def force_populate_nins():
    """Force populate NIN database"""
    try:
        # Clear existing NINs
        NINVerification.query.delete()
        db.session.commit()
        
        # Complete list of demo NINs
        demo_nins = [
            {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15"},
            {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22"},
            {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30"},
            {"nin": "CF321654987GH", "full_name": "Mary Williams", "dob": "1996-03-18"},
            {"nin": "CM789123456IJ", "full_name": "David Brown", "dob": "1994-07-25"},
            {"nin": "CF147258369KL", "full_name": "Sarah Taylor", "dob": "1997-09-12"},
            {"nin": "CM369258147MN", "full_name": "Michael Anderson", "dob": "1993-12-05"},
            {"nin": "CF951753456OP", "full_name": "Elizabeth Thomas", "dob": "1999-01-28"},
            {"nin": "CM159753248QR", "full_name": "James Wilson", "dob": "1991-04-10"},
            {"nin": "CF753951826ST", "full_name": "Patricia Moore", "dob": "1995-08-15"},
        ]
        
        count = 0
        for nin_data in demo_nins:
            nin_record = NINVerification(
                nin=nin_data["nin"],
                full_name=nin_data["full_name"],
                date_of_birth=nin_data["dob"],
                is_valid=True
            )
            db.session.add(nin_record)
            count += 1
            print(f"➕ Added NIN: {nin_data['nin']}")
        
        db.session.commit()
        
        return f"""
        <html>
        <head><title>NIN Database Populated</title></head>
        <body style="font-family: Arial; padding: 40px;">
            <h1 style="color: green;">✅ NIN Database Populated!</h1>
            <p>Successfully added <strong>{count}</strong> demo NINs to the database.</p>
            <h3>Available NINs:</h3>
            <ul>
                <li><strong>CM123456789AB</strong> - John Doe</li>
                <li><strong>CF987654321CD</strong> - Jane Smith</li>
                <li><strong>CM456789123EF</strong> - Robert Johnson</li>
                <li><strong>CF321654987GH</strong> - Mary Williams</li>
                <li><strong>CM789123456IJ</strong> - David Brown</li>
                <li><strong>CF147258369KL</strong> - Sarah Taylor</li>
                <li><strong>CM369258147MN</strong> - Michael Anderson</li>
                <li><strong>CF951753456OP</strong> - Elizabeth Thomas</li>
            </ul>
            <p><a href="/debug-nin-database">Check NIN Database</a> | <a href="/register">Go to Registration</a></p>
        </body>
        </html>
        """
    except Exception as e:
        db.session.rollback()
        return f"<h1>Error: {str(e)}</h1><pre>{traceback.format_exc()}</pre>"




@app.route('/product/<int:product_id>')
def product_detail(product_id):
    from sqlalchemy.orm import joinedload
    
    product = Product.query.options(joinedload(Product.seller)).get_or_404(product_id)
    
    related_products = Product.query.filter(
        Product.category == product.category,
        Product.id != product.id,
        Product.is_active == True
    ).limit(4).all()
    
    wishlist_ids = []
    if 'user_id' in session:
        wishlist_ids = get_wishlist_ids(session['user_id'])
    
    return render_template('product_detail.html', 
                         product=product, 
                         related_products=related_products,
                         wishlist_ids=wishlist_ids,
                         min=min)





@app.route('/complete/google-buyer', methods=['GET', 'POST'])
def complete_google_buyer_registration():
    """Complete registration for Google users as buyers"""
    if 'google_email' not in session:
        flash('Please authenticate with Google first.', 'warning')
        return redirect(url_for('unified_register'))
    
    if request.method == 'POST':
        phone = request.form.get('phone')
        location = request.form.get('location')
        delivery_address = request.form.get('delivery_address')
        
        # Validate phone
        if not phone or not re.match(r'^[0-9]{10}$', phone):
            flash('Please enter a valid 10-digit phone number.', 'danger')
            return render_template('complete_google_buyer.html',
                                 email=session['google_email'],
                                 name=session['google_name'])
        
        try:
            # Check if user already exists (double-check)
            existing_user = User.query.filter_by(email=session['google_email']).first()
            if existing_user:
                # Log them in instead
                session['user_id'] = existing_user.id
                session['user_name'] = existing_user.fullname
                session['user_type'] = existing_user.user_type
                flash(f'✅ Welcome back, {existing_user.fullname}!', 'success')
                return redirect(url_for('home'))
            
            # Generate a random password for Google users
            random_password = secrets.token_urlsafe(12)
            hashed_password = generate_password_hash(random_password)
            
            # Create new buyer account with password
            new_user = User(
                fullname=session['google_name'],
                email=session['google_email'],
                phone=phone,
                location=location,
                delivery_address=delivery_address,
                password=hashed_password,
                user_type='buyer',
                is_active=True,
                profile_image=None  # Don't store Google URL
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # Store the password in session to show user once
            session['temp_password'] = random_password
            
            # Clear session data
            session.pop('google_email', None)
            session.pop('google_name', None)
            session.pop('google_picture', None)
            session.pop('google_registration_type', None)
            session.pop('google_nonce', None)
            
            # Log the user in
            session['user_id'] = new_user.id
            session['user_name'] = new_user.fullname
            session['user_type'] = new_user.user_type
            
            # Merge any session cart
            merge_session_cart_with_user(new_user.id)
            
            flash(f'✅ Welcome to ShopMax, {new_user.fullname}! Your Google account has been linked.', 'success')
            flash(f'🔑 Your temporary password is: {random_password}. Please change it in your profile.', 'info')
            return redirect(url_for('products'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating Google buyer account: {e}")
            traceback.print_exc()
            flash('Error creating account. Please try again.', 'danger')
    
    return render_template('complete_google_buyer.html',
                         email=session['google_email'],
                         name=session['google_name'])



@app.route('/register/buyer', methods=['GET', 'POST'])
def register_buyer():
    if request.method == 'POST':
        email = request.form.get('email')
        
        if not email or not email.endswith('@students.ucu.ac.ug'):
            flash('❌ Students must use their UCU student email (e.g., B22564@students.ucu.ac.ug)', 'danger')
            return render_template('register_buyer.html')
        
        student_part = email.split('@')[0]
        if not re.match(r'^[AB]\d{5}$', student_part):
            flash('❌ Invalid student email format. Use A or B followed by 5 digits (e.g., B22564)', 'danger')
            return render_template('register_buyer.html')
        
        ucu_record = UCUEmail.query.filter_by(email=email, is_active=True).first()
        
        if not ucu_record:
            flash('❌ This email is not recognized in the UCU system. Please use your official UCU student email.', 'danger')
            return render_template('register_buyer.html')
        
        if ucu_record.user_type != 'student':
            flash('❌ This email belongs to UCU staff. Staff cannot register as buyers.', 'danger')
            return render_template('register_buyer.html')
        
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('This email is already registered. Please login instead.', 'warning')
            return render_template('register_buyer.html')
        
        session['verified_ucu_email'] = email
        session['verified_ucu_name'] = ucu_record.full_name
        session['verified_ucu_student'] = ucu_record.student_number
        session['verified_ucu_dept'] = ucu_record.department
        session['verified_ucu_type'] = 'student'
        
        return redirect(url_for('complete_buyer_registration'))
    
    return render_template('register_buyer.html')



@app.route('/register/seller', methods=['GET', 'POST'])
def register_seller():
    if request.method == 'POST':
        user_type = request.form.get('user_type')
        email = request.form.get('email')
        
        if user_type == 'external':
            flash('Google OAuth integration coming soon!', 'info')
            return redirect(url_for('unified_register'))
        
        elif user_type == 'student':
            if not email or not email.endswith('@students.ucu.ac.ug'):
                flash('❌ Students must use their UCU student email (e.g., B22564@students.ucu.ac.ug)', 'danger')
                return redirect(url_for('unified_register'))
            
            student_part = email.split('@')[0]
            if not re.match(r'^[AB]\d{5}$', student_part):
                flash('❌ Invalid student email format. Use A or B followed by 5 digits', 'danger')
                return redirect(url_for('unified_register'))
            
            ucu_record = UCUEmail.query.filter_by(email=email, is_active=True).first()
            if not ucu_record:
                flash('❌ This email is not recognized in the UCU system.', 'danger')
                return redirect(url_for('unified_register'))
            
            if ucu_record.user_type != 'student':
                flash('❌ This email belongs to UCU staff. Use staff seller registration.', 'danger')
                return redirect(url_for('unified_register'))
            
            if User.query.filter_by(email=email).first():
                flash('❌ This email is already registered. Please login instead.', 'warning')
                return redirect(url_for('unified_register'))
            
            session['verified_ucu_email'] = email
            session['verified_ucu_name'] = ucu_record.full_name
            session['verified_ucu_student'] = ucu_record.student_number
            session['verified_ucu_dept'] = ucu_record.department
            session['verified_ucu_type'] = 'student'
            
            return redirect(url_for('complete_seller_registration'))
        
        elif user_type == 'staff':
            if not email or not email.endswith('@ucu.ac.ug') or email.endswith('@students.ucu.ac.ug'):
                flash('❌ Staff must use their UCU staff email (e.g., okello@ucu.ac.ug)', 'danger')
                return redirect(url_for('unified_register'))
            
            ucu_record = UCUEmail.query.filter_by(email=email, is_active=True).first()
            if not ucu_record:
                flash('❌ This email is not recognized in the UCU system.', 'danger')
                return redirect(url_for('unified_register'))
            
            if ucu_record.user_type != 'staff':
                flash('❌ This email belongs to a student. Use student seller registration.', 'danger')
                return redirect(url_for('unified_register'))
            
            if User.query.filter_by(email=email).first():
                flash('❌ This email is already registered. Please login instead.', 'warning')
                return redirect(url_for('unified_register'))
            
            session['verified_ucu_email'] = email
            session['verified_ucu_name'] = ucu_record.full_name
            session['verified_ucu_type'] = 'staff'
            session['verified_ucu_dept'] = ucu_record.department
            session['verified_ucu_title'] = ucu_record.staff_title
            
            return redirect(url_for('complete_seller_registration'))
        
        else:
            flash('❌ Invalid seller type selected.', 'danger')
            return redirect(url_for('unified_register'))
    
    return render_template('register_seller.html')




@app.route('/debug-api-endpoints')
@admin_required
def debug_api_endpoints():
    """Debug API endpoints for orders"""
    html = "<html><head><title>API Debug</title></head><body>"
    html += "<h1>🔧 API Endpoints Debug</h1>"
    
    # Test riders endpoint
    html += "<h2>Testing /api/admin/delivery/persons/available</h2>"
    try:
        persons = DeliveryPerson.query.filter_by(is_active=True).all()
        html += f"<p>Found {len(persons)} active riders:</p>"
        if persons:
            html += "<ul>"
            for p in persons:
                html += f"<li>ID: {p.id} - {p.name} - {p.phone} - {p.vehicle_type}</li>"
            html += "</ul>"
        else:
            html += "<p style='color:red'>❌ No active riders found!</p>"
            html += "<p><a href='/admin/delivery'>Go to Delivery Management to add riders</a></p>"
    except Exception as e:
        html += f"<p style='color:red'>Error: {str(e)}</p>"
    
    # Add test rider link
    html += "<h2>Add Test Riders</h2>"
    html += "<p><a href='/add-test-riders' style='background:#f68b1e; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;'>Add Test Riders</a></p>"
    
    html += "</body></html>"
    return html








@app.route('/admin/fix-categories')
@admin_required
def fix_product_categories():
    """Fix product categories to match the sidebar categories"""
    try:
        # Get all products
        products = Product.query.all()
        
        # Category mapping (what they might be vs what they should be)
        category_map = {
            # Academics
            'book': 'textbooks',
            'books': 'textbooks',
            'textbook': 'textbooks',
            'guide': 'study_guides',
            'study guide': 'study_guides',
            'pen': 'stationery',
            'pencil': 'stationery',
            'notebook': 'stationery',
            
            # Technology
            'computer': 'laptops',
            'laptop': 'laptops',
            'macbook': 'laptops',
            'phone': 'phones',
            'smartphone': 'phones',
            'iphone': 'phones',
            'samsung': 'phones',
            'tablet': 'tablets',
            'ipad': 'tablets',
            'headphone': 'headphones',
            'earphone': 'headphones',
            'charger': 'chargers',
            'cable': 'chargers',
            
            # Clothing
            'shirt': 'mens_clothing',
            't-shirt': 'mens_clothing',
            'trouser': 'mens_clothing',
            'jeans': 'mens_clothing',
            'dress': 'womens_clothing',
            'skirt': 'womens_clothing',
            'blouse': 'womens_clothing',
            'shoe': 'shoes',
            'sneaker': 'shoes',
            'bag': 'bags',
            'backpack': 'bags',
            
            # Dorm
            'bed': 'bedding',
            'sheet': 'bedding',
            'blanket': 'bedding',
            'pillow': 'bedding',
            'chair': 'furniture',
            'table': 'furniture',
            'desk': 'furniture',
            'pot': 'kitchen',
            'pan': 'kitchen',
            'plate': 'kitchen',
            'cup': 'kitchen',
            'box': 'storage',
            'container': 'storage',
            
            # Sports
            'ball': 'sports_gear',
            'football': 'sports_gear',
            'basketball': 'sports_gear',
            'dumbbell': 'gym_equipment',
            'gym': 'gym_equipment',
            
            # Entertainment
            'game': 'gaming',
            'playstation': 'gaming',
            'xbox': 'gaming',
            'guitar': 'music',
            
            # Second-hand indicators
            'used book': 'secondhand_books',
            'used textbook': 'secondhand_books',
            'used laptop': 'secondhand_electronics',
            'used phone': 'secondhand_electronics',
            'used furniture': 'secondhand_furniture'
        }
        
        fixed_count = 0
        for product in products:
            original_category = product.category.lower() if product.category else ''
            
            # If category is empty or generic, try to determine from name
            if not product.category or product.category in ['general', 'other', 'uncategorized']:
                # Try to match from product name
                name_lower = product.name.lower()
                for key, mapped_category in category_map.items():
                    if key in name_lower:
                        product.category = mapped_category
                        fixed_count += 1
                        print(f"Fixed: {product.name} -> {mapped_category}")
                        break
                else:
                    # Default to general category based on price
                    if product.price > 100000:
                        product.category = 'laptops'
                    elif product.price > 50000:
                        product.category = 'phones'
                    elif product.price > 20000:
                        product.category = 'headphones'
                    else:
                        product.category = 'textbooks'
                    fixed_count += 1
                    print(f"Defaulted: {product.name} -> {product.category}")
        
        db.session.commit()
        
        # Update category counts for display
        category_counts = {}
        categories = [
            'textbooks', 'study_guides', 'stationery', 'calculators', 'lab_coats',
            'laptops', 'phones', 'tablets', 'headphones', 'chargers',
            'mens_clothing', 'womens_clothing', 'shoes', 'bags',
            'bedding', 'furniture', 'kitchen', 'storage',
            'sports_gear', 'gym_equipment',
            'gaming', 'music',
            'secondhand_books', 'secondhand_electronics', 'secondhand_furniture'
        ]
        
        for cat in categories:
            count = Product.query.filter_by(category=cat, is_active=True).count()
            category_counts[cat] = count
        
        return f"""
        <html>
        <head><title>Categories Fixed</title></head>
        <body style="font-family: Arial; padding: 40px;">
            <h1 style="color: green;">✅ Product Categories Fixed!</h1>
            <p>Fixed <strong>{fixed_count}</strong> products with proper categories.</p>
            
            <h3>Updated Category Counts:</h3>
            <table border="1" cellpadding="8">
                <tr><th>Category</th><th>Count</th></tr>
        """
        
        for cat, count in category_counts.items():
            html += f"<tr><td>{cat}</td><td>{count}</td></tr>"
        
        html += """
            </table>
            <p><a href="/" style="background: #ff6b00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Go to Homepage</a></p>
        </body>
        </html>
        """
        
    except Exception as e:
        db.session.rollback()
        return f"<h1>Error: {str(e)}</h1>"




@app.route('/api/order/<int:order_id>/assign-boda', methods=['POST'])
@admin_required
def assign_boda_to_order(order_id):
    """Assign a boda rider to an order"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
            
        boda_id = data.get('boda_id')
        if not boda_id:
            return jsonify({'success': False, 'message': 'Rider ID is required'}), 400
        
        order = Order.query.get_or_404(order_id)
        boda = DeliveryPerson.query.get_or_404(boda_id)
        
        # Check if rider is active
        if not boda.is_active:
            return jsonify({'success': False, 'message': 'Selected rider is not active'}), 400
        
        # Check if order already has an assignment
        existing = DeliveryAssignment.query.filter_by(order_id=order_id).first()
        if existing:
            existing.delivery_person_id = boda_id
            existing.status = 'assigned'
            existing.assigned_at = datetime.utcnow()
            assignment = existing
        else:
            assignment = DeliveryAssignment(
                order_id=order_id,
                delivery_person_id=boda_id,
                status='assigned',
                assigned_at=datetime.utcnow()
            )
            db.session.add(assignment)
        
        # Update order status
        order.status = 'shipped'
        order.delivery_person_id = boda_id
        order.delivery_status = 'assigned'
        
        # Add tracking update
        tracking = OrderTracking(
            order_id=order_id,
            status='assigned_to_boda',
            location='Warehouse',
            notes=f'Assigned to delivery person: {boda.name} ({boda.phone})'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Order assigned to {boda.name}',
            'assignment': {
                'id': assignment.id,
                'status': assignment.status,
                'assigned_at': assignment.assigned_at.strftime('%Y-%m-%d %H:%M') if assignment.assigned_at else None
            }
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error assigning boda: {str(e)}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500




@app.route('/api/admin/orders/<int:order_id>/update_status', methods=['POST'])
@admin_required
def update_order_status(order_id):
    """Update order status"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
            
        status = data.get('status')
        if not status:
            return jsonify({'success': False, 'message': 'Status is required'}), 400
        
        order = Order.query.get_or_404(order_id)
        order.status = status
        
        if status == 'delivered' or status == 'completed':
            order.actual_delivery = datetime.utcnow()
        
        # Add tracking update
        tracking = OrderTracking(
            order_id=order_id,
            status=status,
            notes=f'Order status updated to {status}'
        )
        db.session.add(tracking)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Order status updated to {status}',
            'order_id': order.id,
            'status': order.status
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating order status: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500







@app.route('/api/order/<int:order_id>/tracking')
def get_order_tracking_api(order_id):
    """API endpoint for live tracking updates"""
    try:
        order = Order.query.get_or_404(order_id)
        
        # Get latest tracking point
        latest_tracking = DeliveryTracking.query.filter_by(order_id=order_id).order_by(DeliveryTracking.created_at.desc()).first()
        
        # Get all tracking points for history
        tracking_history = DeliveryTracking.query.filter_by(order_id=order_id).order_by(DeliveryTracking.created_at).all()
        
        history = []
        for point in tracking_history:
            history.append({
                'status': point.status,
                'location': point.location,
                'time': point.created_at.strftime('%H:%M, %d %b'),
                'battery': point.battery_level,
                'speed': point.speed
            })
        
        data = {
            'order_id': order.id,
            'order_status': order.status,
            'delivery_address': order.delivery_address,
            'rider_name': order.delivery_person.name if order.delivery_person else None,
            'rider_phone': order.delivery_person.phone if order.delivery_person else None,
            'rider_location': order.delivery_person.current_location if order.delivery_person else None,
            'last_update': order.last_updated.strftime('%H:%M, %d %b') if order.last_updated else None,
            'estimated_delivery': order.estimated_delivery.strftime('%H:%M, %d %b') if order.estimated_delivery else None,
            'tracking_history': history,
            'latest_tracking': {
                'location': latest_tracking.location if latest_tracking else None,
                'battery': latest_tracking.battery_level if latest_tracking else None,
                'time': latest_tracking.created_at.strftime('%H:%M, %d %b') if latest_tracking else None
            } if latest_tracking else None
        }
        
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/delivery/update-location', methods=['POST'])
@login_required
def update_delivery_location_api():
    """API for riders to update their location (called from rider app)"""
    try:
        data = request.get_json()
        rider_id = data.get('rider_id')
        order_id = data.get('order_id')
        location = data.get('location')
        lat = data.get('lat')
        lng = data.get('lng')
        battery = data.get('battery')
        status = data.get('status')
        
        # Verify rider
        rider = DeliveryPerson.query.get(rider_id)
        if not rider:
            return jsonify({'success': False, 'error': 'Rider not found'}), 404
        
        # Update rider's current location
        rider.current_location = location
        rider.updated_at = datetime.utcnow()
        
        # Create tracking point
        tracking = DeliveryTracking(
            order_id=order_id,
            delivery_person_id=rider_id,
            status=status or 'in_transit',
            location=location,
            location_lat=lat,
            location_lng=lng,
            battery_level=battery,
            created_at=datetime.utcnow()
        )
        db.session.add(tracking)
        
        # Update order's last_updated
        order = Order.query.get(order_id)
        if order:
            order.last_updated = datetime.utcnow()
            order.current_location = location
        
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/api/admin/delivery/persons/<int:person_id>/assignments')
@admin_required
def get_delivery_person_assignments(person_id):
    """Get delivery person assignment history"""
    try:
        person = DeliveryPerson.query.get_or_404(person_id)
        
        assignments = []
        for order in person.deliveries:
            assignments.append({
                'id': order.id,
                'order_id': order.id,
                'status': order.status,
                'customer_name': order.user.fullname if order.user else 'N/A',
                'customer_phone': order.user.phone if order.user else 'N/A',
                'assigned_at': order.created_at.isoformat() if order.created_at else None,
                'completed_at': order.actual_delivery.isoformat() if order.actual_delivery else None,
                'delivery_address': order.delivery_address,
                'total_amount': float(order.total_amount) if order.total_amount else 0
            })
        
        # Sort by date (newest first)
        assignments.sort(key=lambda x: x.get('assigned_at', ''), reverse=True)
        
        return jsonify({
            'rider_name': person.name,
            'assignments': assignments
        })
        
    except Exception as e:
        print(f"Error fetching rider assignments: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/admin/delivery-management')
@admin_required
def delivery_management():
    """Admin delivery management page (redirect to tracking)"""
    return redirect(url_for('delivery_tracking'))





@app.route('/admin/boda-rider/add', methods=['POST'])
@admin_required
def add_boda_rider_simp():
    """Simple endpoint to add a boda rider"""
    try:
        name = request.form.get('name')
        phone = request.form.get('phone')
        vehicle_type = request.form.get('vehicle_type', 'motorcycle')
        vehicle_number = request.form.get('vehicle_number')
        current_location = request.form.get('current_location')
        
        # Validate
        if not name or not phone:
            flash('Name and phone are required!', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Check if phone exists
        existing = DeliveryPerson.query.filter_by(phone=phone).first()
        if existing:
            flash('A rider with this phone number already exists!', 'danger')
            return redirect(url_for('boda_riders'))
        
        # Create new rider
        rider = DeliveryPerson(
            name=name,
            phone=phone,
            vehicle_type=vehicle_type,
            vehicle_number=vehicle_number,
            current_location=current_location,
            is_active=True
        )
        
        db.session.add(rider)
        db.session.commit()
        
        flash(f'✅ Rider {name} added successfully!', 'success')
        return redirect(url_for('boda_riders'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding rider: {str(e)}', 'danger')
        return redirect(url_for('boda_riders'))






@app.route('/complete-staff-registration', methods=['GET', 'POST'])
def complete_staff_registration():
    if 'verified_ucu_email' not in session:
        flash('Please verify your UCU email first.', 'warning')
        return redirect(url_for('register_staff'))
    
    email = session['verified_ucu_email']
    ucu_name = session['verified_ucu_name']
    department = session.get('verified_ucu_dept', '')
    title = session.get('verified_ucu_title', '')
    
    if request.method == 'POST':
        phone = request.form.get('phone')
        location = request.form.get('location')
        delivery_address = request.form.get('delivery_address')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('complete_staff_registration.html', 
                                 email=email, name=ucu_name, department=department, title=title)
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('complete_staff_registration.html', 
                                 email=email, name=ucu_name, department=department, title=title)
        
        if not phone or not re.match(r'^[0-9]{10}$', phone):
            flash('Please enter a valid 10-digit phone number.', 'danger')
            return render_template('complete_staff_registration.html', 
                                 email=email, name=ucu_name, department=department, title=title)
        
        try:
            new_user = User(
                fullname=ucu_name,
                email=email,
                phone=phone,
                location=location,
                delivery_address=delivery_address,
                password=generate_password_hash(password),
                user_type='staff',
                is_active=True
            )
            
            db.session.add(new_user)
            db.session.commit()
            
            # Clear session
            session.pop('verified_ucu_email', None)
            session.pop('verified_ucu_name', None)
            session.pop('verified_ucu_dept', None)
            session.pop('verified_ucu_title', None)
            session.pop('verified_ucu_type', None)
            
            session['user_id'] = new_user.id
            session['user_name'] = new_user.fullname
            session['user_type'] = new_user.user_type
            
            flash(f'✅ Welcome to ShopMax, {ucu_name}! Your UCU staff account has been created.', 'success')
            return redirect(url_for('products'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error creating staff account: {e}")
            traceback.print_exc()
            flash('Error creating account. Please try again.', 'danger')
    
    return render_template('complete_staff_registration.html', 
                         email=email, 
                         name=ucu_name,
                         department=department,
                         title=title)






# ==================== NIN MANAGEMENT SYSTEM ====================

# Complete NIN Database with all records
ALL_NINS = [
    # === ORIGINAL 8 DEMO NINS ===
    {"nin": "CM123456789AB", "full_name": "John Doe", "dob": "1995-05-15", "type": "student"},
    {"nin": "CF987654321CD", "full_name": "Jane Smith", "dob": "1998-08-22", "type": "student"},
    {"nin": "CM456789123EF", "full_name": "Robert Johnson", "dob": "1992-11-30", "type": "student"},
    {"nin": "CF321654987GH", "full_name": "Mary Williams", "dob": "1996-03-18", "type": "student"},
    {"nin": "CM789123456IJ", "full_name": "David Brown", "dob": "1994-07-25", "type": "student"},
    {"nin": "CF147258369KL", "full_name": "Sarah Taylor", "dob": "1997-09-12", "type": "student"},
    {"nin": "CM369258147MN", "full_name": "Michael Anderson", "dob": "1993-12-05", "type": "student"},
    {"nin": "CF951753456OP", "full_name": "Elizabeth Thomas", "dob": "1999-01-28", "type": "student"},
    
    # === UCU STUDENT NINS ===
    {"nin": "CM111222333AB", "full_name": "Alice Nambi", "dob": "1999-03-12", "type": "student"},
    {"nin": "CF444555666CD", "full_name": "Brian Ssali", "dob": "1998-07-25", "type": "student"},
    {"nin": "CM777888999EF", "full_name": "Catherine Nalubega", "dob": "2000-01-05", "type": "student"},
    {"nin": "CF123987456GH", "full_name": "David Mukasa", "dob": "1997-11-14", "type": "student"},
    {"nin": "CM456789123IJ", "full_name": "Esther Nakato", "dob": "1999-08-30", "type": "student"},
    {"nin": "CF789123456KL", "full_name": "Frank Mutebi", "dob": "1996-04-22", "type": "student"},
    {"nin": "CM321654987MN", "full_name": "Grace Achieng", "dob": "2000-06-17", "type": "student"},
    {"nin": "CF654987321OP", "full_name": "Henry Okello", "dob": "1995-10-08", "type": "student"},
    {"nin": "CM987321654QR", "full_name": "Irene Namugenyi", "dob": "1998-12-03", "type": "student"},
    {"nin": "CF258147369ST", "full_name": "John Kato", "dob": "1994-02-28", "type": "student"},
    {"nin": "CM555666777AB", "full_name": "Kevin Mukasa", "dob": "2001-03-15", "type": "student"},
    {"nin": "CF888999000CD", "full_name": "Linda Nansamba", "dob": "2000-07-22", "type": "student"},
    {"nin": "CM111222333EF", "full_name": "Moses Ssenyonga", "dob": "1999-11-30", "type": "student"},
    {"nin": "CF444555666GH", "full_name": "Nina Kyomugisha", "dob": "2001-01-18", "type": "student"},
    {"nin": "CM777888999IJ", "full_name": "Oscar Wasswa", "dob": "2000-05-05", "type": "student"},
    {"nin": "CF123456789KL", "full_name": "Phiona Nalule", "dob": "1999-09-09", "type": "student"},
    {"nin": "CM987654321MN", "full_name": "Ronald Ssekandi", "dob": "2000-12-12", "type": "student"},
    {"nin": "CF456789123OP", "full_name": "Sarah Nambi", "dob": "2001-04-04", "type": "student"},
    {"nin": "CM321789654QR", "full_name": "Thomas Mulindwa", "dob": "1998-08-08", "type": "student"},
    {"nin": "CF654321987ST", "full_name": "Umar Segawa", "dob": "2000-02-20", "type": "student"},
    
    # === UCU STAFF NINS ===
    {"nin": "CM147258369AB", "full_name": "Dr. Peter Okello", "dob": "1980-05-15", "type": "staff"},
    {"nin": "CF258369147CD", "full_name": "Prof. Grace Nakato", "dob": "1975-08-22", "type": "staff"},
    {"nin": "CM369147258EF", "full_name": "Dr. James Ssenyonjo", "dob": "1982-11-30", "type": "staff"},
    {"nin": "CF147369258GH", "full_name": "Ms. Sarah Nabatanzi", "dob": "1988-03-18", "type": "staff"},
    {"nin": "CM258147369IJ", "full_name": "Mr. David Kato", "dob": "1985-07-25", "type": "staff"},
    {"nin": "CF369258147KL", "full_name": "Dr. Maria Namugenyi", "dob": "1979-09-12", "type": "staff"},
    {"nin": "CM741852963AB", "full_name": "Prof. John Mukiibi", "dob": "1972-04-08", "type": "staff"},
    {"nin": "CF852963741CD", "full_name": "Dr. Rebecca Nambi", "dob": "1983-11-22", "type": "staff"},
    {"nin": "CM963741852EF", "full_name": "Mr. Charles Lwanga", "dob": "1978-06-14", "type": "staff"},
    {"nin": "CF159753486GH", "full_name": "Dr. Alice Mukasa", "dob": "1981-09-27", "type": "staff"},
    
    # === ADDITIONAL STUDENT NINS ===
    {"nin": "CM159753248QR", "full_name": "James Wilson", "dob": "1995-04-10", "type": "student"},
    {"nin": "CF753951826ST", "full_name": "Patricia Moore", "dob": "1998-08-15", "type": "student"},
    {"nin": "CM852963741UV", "full_name": "Christopher Lee", "dob": "1994-12-20", "type": "student"},
    {"nin": "CF741852963WX", "full_name": "Linda Martinez", "dob": "1997-06-03", "type": "student"},
    {"nin": "CM963852741YZ", "full_name": "Daniel Rodriguez", "dob": "1996-09-18", "type": "student"},
    {"nin": "CM246813579AB", "full_name": "Michael Nsubuga", "dob": "2000-10-15", "type": "student"},
    {"nin": "CF135792468CD", "full_name": "Jennifer Achieng", "dob": "1999-04-22", "type": "student"},
    {"nin": "CM864209753EF", "full_name": "Robert Ssemanda", "dob": "1998-12-01", "type": "student"},
    {"nin": "CF975310864GH", "full_name": "Mary Nalwoga", "dob": "2001-07-19", "type": "student"},
    {"nin": "CM123456789ZA", "full_name": "Joseph Kibuuka", "dob": "1997-02-28", "type": "student"},


    {"nin": "CM123456789AB", "full_name": "Okello John Bosco", "dob": "2000-05-15", "type": "student", "course": "Computer Science", "year": 3},
    {"nin": "CM234567890BC", "full_name": "Mukasa David", "dob": "2001-08-22", "type": "student", "course": "Business Administration", "year": 2},
    {"nin": "CM345678901CD", "full_name": "Ssali James", "dob": "1999-11-30", "type": "student", "course": "Law", "year": 4},
    {"nin": "CM456789012DE", "full_name": "Kato Robert", "dob": "2002-03-18", "type": "student", "course": "Medicine", "year": 1},
    {"nin": "CM567890123EF", "full_name": "Wasswa Henry", "dob": "2000-07-25", "type": "student", "course": "Engineering", "year": 3},
    {"nin": "CM678901234FG", "full_name": "Ssenyonga Peter", "dob": "2001-09-12", "type": "student", "course": "Education", "year": 2},
    {"nin": "CM789012345GH", "full_name": "Muwanga Isaac", "dob": "1998-12-05", "type": "student", "course": "Pharmacy", "year": 4},
    {"nin": "CM890123456HI", "full_name": "Tumusiime Andrew", "dob": "2002-01-28", "type": "student", "course": "Nursing", "year": 1},
    {"nin": "CM901234567IJ", "full_name": "Lwanga Charles", "dob": "2000-04-10", "type": "student", "course": "Social Sciences", "year": 3},
    {"nin": "CM012345678JK", "full_name": "Okot Moses", "dob": "1999-08-15", "type": "student", "course": "Economics", "year": 4},


        {"nin": "CF777888999MN", "full_name": "Nantongo Grace", "dob": "2001-09-09", "type": "student", "course": "Medicine", "year": 2},
    {"nin": "CF888999000OP", "full_name": "Nakimuli Florence", "dob": "1998-11-21", "type": "student", "course": "Pharmacy", "year": 4},
    {"nin": "CF999000111QR", "full_name": "Namazzi Katherine", "dob": "2000-04-17", "type": "student", "course": "Nursing", "year": 3},
    {"nin": "CF000111222ST", "full_name": "Amoding Esther", "dob": "2001-08-08", "type": "student", "course": "Computer Science", "year": 2},
    
    # === UCU STAFF NINS ===
    {"nin": "CM147258369AB", "full_name": "Dr. Peter Okello", "dob": "1980-05-15", "type": "staff", "title": "Senior Lecturer", "department": "Computer Science"},
    {"nin": "CF258369147CD", "full_name": "Prof. Grace Nakato", "dob": "1975-08-22", "type": "staff", "title": "Professor", "department": "Law"},
    {"nin": "CM369147258EF", "full_name": "Dr. James Ssenyonjo", "dob": "1982-11-30", "type": "staff", "title": "Associate Professor", "department": "Business"},
    {"nin": "CF147369258GH", "full_name": "Ms. Sarah Nabatanzi", "dob": "1988-03-18", "type": "staff", "title": "Lecturer", "department": "Education"},
    {"nin": "CM258147369IJ", "full_name": "Mr. David Kato", "dob": "1985-07-25", "type": "staff", "title": "Senior Lecturer", "department": "Engineering"},
    {"nin": "CF369258147KL", "full_name": "Dr. Maria Namugenyi", "dob": "1979-09-12", "type": "staff", "title": "Senior Consultant", "department": "Medicine"},
    {"nin": "CM741852963AB", "full_name": "Prof. John Mukiibi", "dob": "1972-04-08", "type": "staff", "title": "Professor", "department": "Economics"},
    {"nin": "CF852963741CD", "full_name": "Dr. Rebecca Nambi", "dob": "1983-11-22", "type": "staff", "title": "Senior Lecturer", "department": "Pharmacy"},
    {"nin": "CM963741852EF", "full_name": "Mr. Charles Lwanga", "dob": "1978-06-14", "type": "staff", "title": "Lecturer", "department": "Nursing"},
    {"nin": "CF159753486GH", "full_name": "Dr. Alice Mukasa", "dob": "1981-09-27", "type": "staff", "title": "Associate Professor", "department": "Social Sciences"},
    {"nin": "CM456123789AB", "full_name": "Dr. Robert Wasswa", "dob": "1984-02-18", "type": "staff", "title": "Senior Lecturer", "department": "Computer Science"},
    {"nin": "CF789456123CD", "full_name": "Prof. Martha Achieng", "dob": "1976-10-05", "type": "staff", "title": "Professor", "department": "Business"},
    {"nin": "CM321987654EF", "full_name": "Dr. Joseph Kibuuka", "dob": "1987-07-12", "type": "staff", "title": "Lecturer", "department": "Law"},
    {"nin": "CF654321987GH", "full_name": "Ms. Florence Nambi", "dob": "1990-03-25", "type": "staff", "title": "Assistant Lecturer", "department": "Education"},
    {"nin": "CM987654321IJ", "full_name": "Dr. Michael Nsubuga", "dob": "1982-12-03", "type": "staff", "title": "Senior Lecturer", "department": "Engineering"},
    
    # === ADDITIONAL TEST NINS FOR SELLERS ===
    {"nin": "CM123987456AB", "full_name": "Ssempijja John", "dob": "1999-06-15", "type": "student", "course": "Computer Science", "year": 3},
    {"nin": "CF456789123CD", "full_name": "Nakato Jane", "dob": "2000-09-22", "type": "student", "course": "Business", "year": 2},
    {"nin": "CM789456123EF", "full_name": "Mukasa Peter", "dob": "2001-01-30", "type": "student", "course": "Law", "year": 1},
    {"nin": "CF123789456GH", "full_name": "Kyomugisha Grace", "dob": "1998-04-18", "type": "student", "course": "Medicine", "year": 4},
    {"nin": "CM456123789IJ", "full_name": "Ssekandi Ronald", "dob": "2000-11-25", "type": "student", "course": "Engineering", "year": 2},
    {"nin": "CF789456123KL", "full_name": "Nalubega Catherine", "dob": "2001-07-12", "type": "student", "course": "Pharmacy", "year": 2},
    {"nin": "CM321654987MN", "full_name": "Tumusiime Allan", "dob": "1999-03-05", "type": "student", "course": "Nursing", "year": 3},
    {"nin": "CF987321654OP", "full_name": "Achieng Sarah", "dob": "2002-10-28", "type": "student", "course": "Social Sciences", "year": 1},
    {"nin": "CM654987321QR", "full_name": "Kato Brian", "dob": "2000-05-14", "type": "student", "course": "Economics", "year": 2},
    {"nin": "CF321789654ST", "full_name": "Nambi Esther", "dob": "2001-08-19", "type": "student", "course": "Computer Science", "year": 2},
    
    # === SELLER TEST NINS (Specifically for testing seller registration) ===
    {"nin": "CM111222333UV", "full_name": "Test Seller One", "dob": "1995-01-01", "type": "seller_test", "note": "For testing seller registration"},
    {"nin": "CF222333444WX", "full_name": "Test Seller Two", "dob": "1996-02-02", "type": "seller_test", "note": "For testing seller registration"},
    {"nin": "CM333444555YZ", "full_name": "Test Seller Three", "dob": "1997-03-03", "type": "seller_test", "note": "For testing seller registration"},
    {"nin": "CF444555666ZA", "full_name": "Test Seller Four", "dob": "1998-04-04", "type": "seller_test", "note": "For testing seller registration"},
    
    
]

@app.route('/admin/nins')
@admin_required
def manage_nins():
    """Professional NIN Management Dashboard"""
    try:
        # Get all NINs from database
        nins = NINVerification.query.order_by(NINVerification.id).all()
        
        # Calculate stats
        total_nins = len(nins)
        student_count = sum(1 for nin in nins if nin.full_name.startswith(('Alice', 'Brian', 'Catherine', 'David', 'Esther', 'Frank', 'Grace', 'Henry', 'Irene', 'John', 'Kevin', 'Linda', 'Moses', 'Nina', 'Oscar', 'Phiona', 'Ronald', 'Sarah', 'Thomas', 'Umar', 'James', 'Patricia', 'Christopher', 'Daniel', 'Michael', 'Jennifer', 'Robert', 'Mary', 'Joseph')))
        staff_count = total_nins - student_count
        
        return render_template('admin_nins.html', 
                             nins=nins, 
                             total_nins=total_nins,
                             student_count=student_count,
                             staff_count=staff_count,
                             now=datetime.utcnow)
                             
    except Exception as e:
        flash(f'Error loading NINs: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))


@app.route('/api/nins/populate', methods=['POST'])
@admin_required
def populate_nins():
    """Populate NIN database with all records"""
    try:
        added = 0
        skipped = 0
        
        for nin_data in ALL_NINS:
            existing = NINVerification.query.filter_by(nin=nin_data["nin"]).first()
            if not existing:
                nin = NINVerification(
                    nin=nin_data["nin"],
                    full_name=nin_data["full_name"],
                    date_of_birth=nin_data["dob"],
                    is_valid=True
                )
                db.session.add(nin)
                added += 1
            else:
                skipped += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Added {added} new NINs, skipped {skipped} existing',
            'total': NINVerification.query.count()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/nins/reset', methods=['POST'])
@admin_required
def reset_nins():
    """Reset NIN database (clear all and add fresh)"""
    try:
        # Clear existing
        NINVerification.query.delete()
        
        # Add all fresh
        for nin_data in ALL_NINS:
            nin = NINVerification(
                nin=nin_data["nin"],
                full_name=nin_data["full_name"],
                date_of_birth=nin_data["dob"],
                is_valid=True
            )
            db.session.add(nin)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Reset complete! {len(ALL_NINS)} NINs added',
            'total': len(ALL_NINS)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/nins/export')
@admin_required
def export_nins():
    """Export all NINs to CSV"""
    try:
        nins = NINVerification.query.all()
        
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['ID', 'NIN Number', 'Full Name', 'Date of Birth', 'Status', 'Created'])
        
        for nin in nins:
            writer.writerow([
                nin.id,
                nin.nin,
                nin.full_name,
                nin.date_of_birth or 'N/A',
                'Valid' if nin.is_valid else 'Invalid',
                nin.created_at.strftime('%Y-%m-%d %H:%M') if nin.created_at else 'N/A'
            ])
        
        output.seek(0)
        
        response = make_response(output.getvalue())
        response.headers['Content-Disposition'] = f'attachment; filename=nins_export_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        return response
        
    except Exception as e:
        flash(f'Error exporting: {str(e)}', 'danger')
        return redirect(url_for('manage_nins'))



@app.route('/debug-nin-status')
def debug_nin_status():
    """Check NIN database status"""
    try:
        count = NINVerification.query.count()
        nins = NINVerification.query.all()
        
        html = f"""
        <html>
        <head><title>NIN Database Status</title></head>
        <body style="font-family: Arial; padding: 40px;">
            <h1>🔍 NIN Database Status</h1>
            <p><strong>Total NIN records:</strong> {count}</p>
        """
        
        if count == 0:
            html += "<p style='color:red'>❌ No NINs found in database!</p>"
            html += "<p><a href='/reset-nin-database' style='background:#ff6b00; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;'>Reset NIN Database</a></p>"
        else:
            html += "<h3>NIN Records:</h3>"
            html += "<table border='1' cellpadding='8'>"
            html += "<tr><th>ID</th><th>NIN</th><th>Full Name</th><th>Valid</th></tr>"
            for nin in nins:
                html += f"<tr>"
                html += f"<td>{nin.id}</td>"
                html += f"<td><strong>{nin.nin}</strong></td>"
                html += f"<td>{nin.full_name}</td>"
                html += f"<td>{'✅' if nin.is_valid else '❌'}</td>"
                html += f"</tr>"
            html += "</table>"
        
        html += "<p><a href='/reset-nin-database' style='background:#ff6b00; color:white; padding:10px 20px; text-decoration:none; border-radius:5px; margin-right:10px;'>Reset NIN Database</a>"
        html += "<a href='/register' style='background:#1a237e; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;'>Go to Register</a></p>"
        html += "</body></html>"
        return html
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1>"









# ==================== LOGOUT ROUTE ====================
@app.route('/logout')
def logout():
    """Log out the current user"""
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('home'))



@app.route('/profile')
@login_required
def profile():
    """User profile page"""
    user = get_current_user()
    
    if not user:
        flash('Please log in to view your profile.', 'warning')
        return redirect(url_for('login'))
    
    # Get store stats for sellers
    store_stats = {}
    if user.user_type == 'seller':
        products_count = Product.query.filter_by(seller_id=user.id, is_active=True).count()
        total_sales = db.session.query(db.func.sum(OrderItem.quantity)).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed'
        ).scalar() or 0
        
        total_revenue = db.session.query(db.func.sum(OrderItem.price * OrderItem.quantity)).join(Order).filter(
            OrderItem.seller_id == user.id,
            Order.status == 'completed'
        ).scalar() or 0
        
        # Get average rating
        reviews = db.session.query(Review).join(Product).filter(
            Product.seller_id == user.id
        ).all()
        
        if reviews:
            avg_rating = sum(r.rating for r in reviews) / len(reviews)
        else:
            avg_rating = 0
            
        store_stats = {
            'products': products_count,
            'sales': total_sales,
            'revenue': total_revenue,
            'rating': round(avg_rating, 1),
            'reviews': len(reviews)
        }
    
    # Check if there's a temporary password to show
    temp_password = session.pop('temp_password', None)
    
    return render_template('profile.html', 
                         user=user, 
                         store_stats=store_stats, 
                         temp_password=temp_password,
                         now=datetime.utcnow)



@app.route('/debug/all-nins')
@admin_required
def get_all_nins():
    """Get all NINs in the demo database"""
    try:
        nins = NINVerification.query.all()
        
        if not nins:
            return """
            <html>
            <head><title>No NINs Found</title></head>
            <body style="font-family: Arial; padding: 40px;">
                <h1>❌ No NINs Found in Database</h1>
                <p>The NIN verification table is empty.</p>
                <a href="/reset-nin-database" style="background: #ff6b00; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Populate NIN Database</a>
                <a href="/admin/dashboard" style="background: #000080; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">Back to Admin</a>
            </body>
            </html>
            """
        
        # Build HTML table
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>NIN Database - ShopMax</title>
            <style>
                body {
                    font-family: 'Segoe UI', Arial, sans-serif;
                    padding: 40px;
                    background: #f5f5f5;
                }
                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 16px;
                    padding: 30px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                }
                h1 {
                    color: #000080;
                    margin-bottom: 10px;
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }
                .stats {
                    background: #f8fafc;
                    padding: 15px;
                    border-radius: 8px;
                    margin-bottom: 20px;
                    border-left: 4px solid #ff6b00;
                }
                table {
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 20px;
                }
                th {
                    background: #000080;
                    color: white;
                    padding: 12px;
                    text-align: left;
                    font-weight: 600;
                }
                td {
                    padding: 10px;
                    border-bottom: 1px solid #e2e8f0;
                }
                tr:hover {
                    background: #f8fafc;
                }
                .nin-code {
                    font-family: monospace;
                    font-weight: 600;
                    color: #ff6b00;
                }
                .btn {
                    display: inline-block;
                    padding: 8px 16px;
                    background: #ff6b00;
                    color: white;
                    text-decoration: none;
                    border-radius: 6px;
                    margin-right: 10px;
                    font-size: 0.85rem;
                }
                .btn-secondary {
                    background: #000080;
                }
                .btn-secondary:hover {
                    background: #000066;
                }
                .btn:hover {
                    transform: translateY(-2px);
                }
                .actions {
                    margin-top: 20px;
                    padding-top: 20px;
                    border-top: 1px solid #e2e8f0;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>
                    <i class="fas fa-id-card"></i> 
                    NIN Database
                </h1>
        """
        
        # Add stats
        html += f"""
                <div class="stats">
                    <strong>Total NINs:</strong> {len(nins)} verified entries
                </div>
                
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>NIN Number</th>
                            <th>Full Name</th>
                            <th>Date of Birth</th>
                            <th>Valid</th>
                            <th>Created</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        
        for nin in nins:
            html += f"""
                        <tr>
                            <td>{nin.id}</td>
                            <td class="nin-code"><strong>{nin.nin}</strong></td>
                            <td>{nin.full_name}</td>
                            <td>{nin.date_of_birth or 'N/A'}</td>
                            <td>{"✅ Yes" if nin.is_valid else "❌ No"}</td>
                            <td>{nin.created_at.strftime('%d %b %Y') if nin.created_at else 'N/A'}</td>
                        </tr>
            """
        
        html += """
                    </tbody>
                </table>
                
                <div class="actions">
                    <a href="/reset-nin-database" class="btn" onclick="return confirm('Reset all NINs? This will clear existing data.')">
                        🔄 Reset NIN Database
                    </a>
                    <a href="/debug-nin-status" class="btn btn-secondary">
                        🔍 Check NIN Status
                    </a>
                    <a href="/admin/dashboard" class="btn btn-secondary">
                        ← Back to Admin
                    </a>
                </div>
                
                <div style="margin-top: 20px; padding: 15px; background: #fff3e0; border-radius: 8px;">
                    <strong>💡 Tip:</strong> Use these NINs when registering as a seller. They are demo NINs for testing purposes.
                </div>
            </div>
            
            <script src="https://kit.fontawesome.com/your-code.js"></script>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        return f"<h1>Error: {str(e)}</h1><pre>{traceback.format_exc()}</pre>"






def send_password_reset_email(email, token, user_name=None):
    """Send password reset email with proper Gmail SMTP"""
    try:
        # Email configuration
        smtp_server = "smtp.gmail.com"
        port = 587
        sender_email = app.config['MAIL_USERNAME']
        password = app.config['MAIL_PASSWORD']
        
        # Validate configuration
        if not sender_email or not password:
            print("❌ Email configuration missing. Check MAIL_USERNAME and MAIL_PASSWORD in .env")
            return False
        
        # Create reset link
        reset_link = url_for('reset_password', token=token, _external=True)
        
        # Create message
        message = MIMEMultipart("alternative")
        message["Subject"] = "Reset Your Password - ShopMax UCU Marketplace"
        message["From"] = f"ShopMax <{sender_email}>"
        message["To"] = email
        
        # Plain text version
        text = f"""
        Hello {user_name or 'there'},
        
        We received a request to reset your password for your ShopMax account.
        
        Click the link below to reset your password:
        {reset_link}
        
        This link will expire in 1 hour.
        
        If you didn't request a password reset, please ignore this email.
        
        For your security:
        • Never share this link with anyone
        • ShopMax staff will never ask for your password
        
        © {datetime.utcnow().year} ShopMax UCU Marketplace
        """
        
        # HTML version with your branding
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: 'Inter', Arial, sans-serif;
                    background: #f5f5f5;
                    margin: 0;
                    padding: 0;
                }}
                .container {{
                    max-width: 600px;
                    margin: 20px auto;
                    background: white;
                    border-radius: 16px;
                    overflow: hidden;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #000080, #000066);
                    padding: 30px;
                    text-align: center;
                    border-bottom: 3px solid #ff6b00;
                }}
                .header h1 {{
                    color: white;
                    margin: 0;
                    font-size: 28px;
                    font-weight: 700;
                }}
                .header h1 span {{
                    color: #ff6b00;
                }}
                .content {{
                    padding: 40px 30px;
                    background: white;
                }}
                .content h2 {{
                    color: #000080;
                    font-size: 20px;
                    margin-bottom: 20px;
                }}
                .content p {{
                    color: #64748b;
                    line-height: 1.6;
                    margin-bottom: 20px;
                    font-size: 15px;
                }}
                .button {{
                    display: inline-block;
                    padding: 14px 35px;
                    background: #ff6b00;
                    color: white;
                    text-decoration: none;
                    border-radius: 40px;
                    font-weight: 600;
                    font-size: 16px;
                    margin: 20px 0;
                    box-shadow: 0 4px 12px rgba(255,107,0,0.3);
                }}
                .button:hover {{
                    background: #e65100;
                }}
                .link-box {{
                    background: #f8fafc;
                    border: 1px solid #e2e8f0;
                    border-radius: 10px;
                    padding: 15px;
                    margin: 20px 0;
                    word-break: break-all;
                    font-size: 14px;
                    color: #1e293b;
                }}
                .footer {{
                    padding: 30px;
                    text-align: center;
                    background: #f8fafc;
                    border-top: 1px solid #e2e8f0;
                }}
                .footer p {{
                    color: #64748b;
                    font-size: 13px;
                    margin: 5px 0;
                }}
                .footer .ucu {{
                    color: #ff6b00;
                    font-weight: 600;
                }}
                .security-tips {{
                    background: #fff3e0;
                    border-left: 4px solid #ff6b00;
                    padding: 15px;
                    margin-top: 30px;
                    border-radius: 8px;
                }}
                .security-tips p {{
                    margin: 5px 0;
                    font-size: 13px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Shop<span>Max</span></h1>
                </div>
                <div class="content">
                    <h2>Reset Your Password</h2>
                    <p>Hello {user_name or 'there'},</p>
                    <p>We received a request to reset your password for your ShopMax account. Click the button below to create a new password:</p>
                    
                    <div style="text-align: center;">
                        <a href="{reset_link}" class="button">Reset Password</a>
                    </div>
                    
                    <p>Or copy and paste this link into your browser:</p>
                    <div class="link-box">{reset_link}</div>
                    
                    <p><strong>⏰ This link will expire in 1 hour.</strong></p>
                    
                    <div class="security-tips">
                        <p><strong>🔒 Security Tips:</strong></p>
                        <p>• Never share this link with anyone</p>
                        <p>• ShopMax staff will never ask for your password</p>
                        <p>• Make sure you're on the official ShopMax website</p>
                        <p>• If you didn't request this, please ignore this email</p>
                    </div>
                </div>
                <div class="footer">
                    <p>© {datetime.utcnow().year} <span class="ucu">ShopMax UCU Marketplace</span></p>
                    <p>Uganda Christian University, Mukono</p>
                    <p style="font-size: 11px;">This is an automated message, please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Attach both versions
        message.attach(MIMEText(text, "plain"))
        message.attach(MIMEText(html, "html"))
        
        # Send email with timeout
        print(f"📧 Attempting to send email to {email} via Gmail SMTP...")
        
        with smtplib.SMTP(smtp_server, port, timeout=30) as server:
            server.starttls()
            server.login(sender_email, password)
            server.send_message(message)
        
        print(f"✅ Password reset email sent successfully to {email}")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Gmail authentication failed: {e}")
        print("   Please check your MAIL_USERNAME and MAIL_PASSWORD in .env")
        print("   For Gmail, you need to use an App Password (16 chars) not your regular password")
        return False
        
    except smtplib.SMTPException as e:
        print(f"❌ SMTP error: {e}")
        return False
        
    except Exception as e:
        print(f"❌ Unexpected error sending email: {e}")
        traceback.print_exc()
        return False


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Handle password reset with token"""
    # Find valid reset token
    reset = PasswordReset.query.filter_by(
        token=token,
        used=False
    ).first()
    
    if not reset:
        flash('Invalid or expired password reset link.', 'danger')
        return redirect(url_for('forgot_password'))
    
    # Check if token has expired
    if reset.expires_at < datetime.utcnow():
        reset.used = True
        db.session.commit()
        flash('Password reset link has expired. Please request a new one.', 'danger')
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate password
        if not password or len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('reset_password.html', token=token)
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', token=token)
        
        try:
            # Find user and update password
            user = User.query.filter_by(email=reset.email).first()
            
            if not user:
                flash('User account not found.', 'danger')
                return redirect(url_for('register_buyer'))
            
            user.password = generate_password_hash(password)
            user.updated_at = datetime.utcnow()
            
            # Mark token as used
            reset.used = True
            
            db.session.commit()
            
            flash('Your password has been reset successfully! Please login with your new password.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error in reset_password: {e}")
            traceback.print_exc()
            flash('An error occurred. Please try again.', 'danger')
            return render_template('reset_password.html', token=token)
    
    return render_template('reset_password.html', token=token)










@app.route('/debug-products')
@admin_required
def debug_products():
    """Check what products exist in database"""
    products = Product.query.all()
    
    html = "<h1>📦 Products in Database</h1>"
    html += f"<p>Total products: {len(products)}</p>"
    
    if products:
        html += "<table border='1' cellpadding='5'>"
        html += "<tr><th>ID</th><th>Name</th><th>Category</th><th>Price</th><th>Stock</th><th>Active</th><th>Seller</th></tr>"
        for p in products:
            html += f"<tr>"
            html += f"<td>{p.id}</td>"
            html += f"<td>{p.name}</td>"
            html += f"<td>{p.category}</td>"
            html += f"<td>{p.price}</td>"
            html += f"<td>{p.stock}</td>"
            html += f"<td>{p.is_active}</td>"
            html += f"<td>{p.seller_id}</td>"
            html += f"</tr>"
        html += "</table>"
    else:
        html += "<p style='color:red'>❌ No products found!</p>"
    
    return html



@app.route('/seller/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    user = get_current_user()
    
    if user.user_type != 'seller' or not has_active_subscription(user):
        flash('Please subscribe to a plan to edit products.', 'info')
        return redirect(url_for('seller_subscription'))
    
    product = Product.query.get_or_404(product_id)
    
    if product.seller_id != user.id:
        flash('You can only edit your own products.', 'danger')
        return redirect(url_for('manage_products'))
    
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', '').strip()
            category = request.form.get('category', '').strip()
            stock = request.form.get('stock', '1').strip()
            brand = request.form.get('brand', '').strip()
            condition = request.form.get('condition', 'new')
            
            if not all([name, description, price, category]):
                flash('Please fill in all required fields: Name, Description, Price, and Category.', 'danger')
                return render_template('edit_product.html', product=product)
            
            image_file = request.files.get('image')
            
            if image_file and image_file.filename:
                if allowed_file(image_file.filename):
                    ensure_upload_folder()
                    filename = secure_filename(image_file.filename)
                    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
                    image_filename = timestamp + filename
                    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                    image_file.save(image_path)
                    
                    if product.image:
                        old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], product.image)
                        if os.path.exists(old_image_path):
                            os.remove(old_image_path)
                    
                    product.image = image_filename
                else:
                    flash('Please upload JPG, PNG, or GIF images only.', 'danger')
                    return render_template('edit_product.html', product=product)
            
            product.name = name
            product.description = description
            product.price = float(price)
            product.category = category
            product.stock = int(stock) if stock else 1
            product.brand = brand if brand else None
            product.condition = condition
            product.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            flash('✅ Product updated successfully!', 'success')
            return redirect(url_for('manage_products'))
            
        except ValueError as e:
            flash('Please enter valid price and stock values.', 'danger')
            return render_template('edit_product.html', product=product)
        except Exception as e:
            db.session.rollback()
            flash('Error updating product. Please try again.', 'danger')
            return render_template('edit_product.html', product=product)
    
    return render_template('edit_product.html', product=product)

@app.route('/seller/products/<int:product_id>/toggle')
@login_required
def toggle_product(product_id):
    user = get_current_user()
    
    product = Product.query.get_or_404(product_id)
    
    if product.seller_id != user.id:
        flash('You can only manage your own products.', 'danger')
        return redirect(url_for('manage_products'))
    
    try:
        product.is_active = not product.is_active
        db.session.commit()
        
        status = "activated" if product.is_active else "deactivated"
        flash(f'Product {status} successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Error updating product status.', 'danger')
    
    return redirect(url_for('manage_products'))

@app.route('/seller/products/<int:product_id>/delete', methods=['POST'])
@login_required
def delete_product(product_id):
    user = get_current_user()
    
    product = Product.query.get_or_404(product_id)
    
    if product.seller_id != user.id:
        flash('You can only delete your own products.', 'danger')
        return redirect(url_for('manage_products'))
    
    try:
        if product.image:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], product.image)
            if os.path.exists(image_path):
                os.remove(image_path)
        
        db.session.delete(product)
        db.session.commit()
        
        flash('Product deleted successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash('Error deleting product. Please try again.', 'danger')
    
    return redirect(url_for('manage_products'))





# ==================== SELLER STORE ROUTES ====================
@app.route('/seller/<int:seller_id>/store')
def seller_store(seller_id):
    seller = User.query.get_or_404(seller_id)
    
    if seller.user_type != 'seller':
        flash('This user is not a seller.', 'danger')
        return redirect(url_for('products'))
    
    products = Product.query.filter_by(
        seller_id=seller_id, 
        is_active=True
    ).order_by(Product.created_at.desc()).all()
    
    total_products = len(products)
    total_sales = db.session.query(db.func.sum(OrderItem.quantity)).join(OrderItem.product).filter(
        Product.seller_id == seller_id,
        OrderItem.order.has(status='completed')
    ).scalar() or 0
    
    avg_rating = db.session.query(db.func.avg(Review.rating)).join(Review.product).filter(
        Product.seller_id == seller_id
    ).scalar() or 0
    avg_rating = round(avg_rating, 1)
    
    wishlist_ids = []
    if 'user_id' in session:
        wishlist_ids = get_wishlist_ids(session['user_id'])
    
    return render_template('seller_store.html',
                         seller=seller,
                         products=products,
                         total_products=total_products,
                         total_sales=total_sales,
                         avg_rating=avg_rating,
                         wishlist_ids=wishlist_ids)

@app.route('/seller/store-settings', methods=['GET', 'POST'])
@login_required
def store_settings():
    user = get_current_user()
    
    if user.user_type != 'seller':
        flash('This page is only for sellers.', 'danger')
        return redirect(url_for('dashboard'))
    
    if not has_active_subscription(user):
        flash('Please subscribe to a plan to access store settings.', 'info')
        return redirect(url_for('seller_subscription'))
    
    if request.method == 'POST':
        try:
            user.business_name = request.form.get('business_name', user.business_name)
            user.business_description = request.form.get('business_description', user.business_description)
            user.business_phone = request.form.get('business_phone', user.business_phone)
            user.business_email = request.form.get('business_email', user.business_email)
            user.business_address = request.form.get('business_address', user.business_address)
            user.business_type = request.form.get('business_type', user.business_type)
            
            if 'store_logo' in request.files:
                file = request.files['store_logo']
                if file and file.filename and allowed_file(file.filename):
                    if user.profile_image:
                        old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], user.profile_image)
                        if os.path.exists(old_image_path):
                            os.remove(old_image_path)
                    
                    filename = secure_filename(file.filename)
                    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
                    new_filename = 'store_' + timestamp + filename
                    ensure_upload_folder()
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], new_filename))
                    user.profile_image = new_filename
            
            user.updated_at = datetime.utcnow()
            db.session.commit()
            
            flash('Store settings updated successfully!', 'success')
            return redirect(url_for('store_settings'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating store settings: {str(e)}', 'danger')
    
    products_count = Product.query.filter_by(seller_id=user.id, is_active=True).count()
    total_orders = db.session.query(db.func.count(db.distinct(Order.id))).join(OrderItem).filter(
        OrderItem.seller_id == user.id
    ).scalar() or 0
    
    total_revenue = db.session.query(db.func.sum(OrderItem.price * OrderItem.quantity)).join(Order).filter(
        OrderItem.seller_id == user.id,
        Order.status == 'completed'
    ).scalar() or 0
    
    avg_rating = db.session.query(db.func.avg(Review.rating)).join(Product).filter(
        Product.seller_id == user.id
    ).scalar() or 0
    avg_rating = round(avg_rating, 1)
    
    store_stats = {
        'products_count': products_count,
        'total_orders': total_orders,
        'total_revenue': total_revenue,
        'avg_rating': avg_rating
    }
    
    return render_template('store_settings.html', user=user, store_stats=store_stats)



@app.route('/delivery/pending-confirmations')
@login_required
def pending_delivery_confirmation():
    """Redirect to the first order needing delivery confirmation"""
    user = get_current_user()
    
    if user.user_type != 'buyer':
        flash('Only buyers can access this page.', 'danger')
        return redirect(url_for('home'))
    
    # Get the most recent delivered order that needs confirmation
    order = Order.query.filter_by(
        user_id=user.id,
        status='delivered'
    ).order_by(Order.created_at.desc()).first()
    
    if not order:
        flash('No orders need confirmation at this time.', 'info')
        return redirect(url_for('orders'))
    
    # Redirect to the single order confirmation page
    return redirect(url_for('delivery_confirmation', order_id=order.id))


# ==================== CART ROUTES ====================
@app.route('/cart')
def view_cart():
    if 'user_id' in session:
        user = get_current_user()
        
        if user.user_type != 'buyer':
            flash('Cart is only available for buyers.', 'info')
            return redirect(url_for('products'))
        
        from sqlalchemy.orm import joinedload
        cart_items = db.session.query(Cart, Product).\
            join(Product, Cart.product_id == Product.id).\
            options(joinedload(Product.seller)).\
            filter(Cart.user_id == user.id).\
            all()
    else:
        session_cart = session.get('cart', {})
        cart_items = []
        
        if session_cart:
            product_ids = []
            for pid in session_cart.keys():
                try:
                    product_ids.append(int(pid))
                except ValueError:
                    continue
            
            if product_ids:
                from sqlalchemy.orm import joinedload
                products = Product.query.filter(Product.id.in_(product_ids)).\
                    options(joinedload(Product.seller)).all()
                
                for product in products:
                    quantity = session_cart.get(str(product.id), 0)
                    if quantity > 0:
                        cart_item = {
                            'id': f"session_{product.id}",
                            'quantity': quantity
                        }
                        cart_items.append((cart_item, product))
    
    subtotal = 0
    for cart_item, product in cart_items:
        if isinstance(cart_item, dict):
            quantity = cart_item['quantity']
        elif hasattr(cart_item, 'quantity'):
            quantity = cart_item.quantity
        else:
            quantity = 0
        subtotal += product.price * quantity
    
    delivery_fee = 5000
    total = subtotal + delivery_fee
    
    return render_template('cart.html', 
                         cart_items=cart_items, 
                         subtotal=subtotal,
                         delivery_fee=delivery_fee,
                         total=total)

# ==================== CHECKOUT & ORDER ROUTES ====================
@app.route('/checkout')
def checkout():
    if 'user_id' not in session:
        session_cart = session.get('cart', {})
        if not session_cart:
            flash('Your cart is empty. Add some products before checkout.', 'info')
            return redirect(url_for('view_cart'))
        
        session['next_url'] = url_for('checkout')
        flash('Please login or create an account to proceed to checkout.', 'info')
        return redirect(url_for('login'))
    
    user = get_current_user()
    
    if user.user_type != 'buyer':
        flash('Checkout is only available for buyers.', 'info')
        return redirect(url_for('products'))
    
    from sqlalchemy.orm import joinedload
    
    cart_items = db.session.query(Cart, Product).\
        join(Product, Cart.product_id == Product.id).\
        options(joinedload(Product.seller)).\
        filter(Cart.user_id == user.id).\
        all()
    
    if not cart_items:
        flash('Your cart is empty. Add some products before checkout.', 'info')
        return redirect(url_for('view_cart'))
    
    for cart_item, product in cart_items:
        if product.stock < cart_item.quantity:
            flash(f'Sorry, "{product.name}" only has {product.stock} items in stock. Please adjust your quantity.', 'danger')
            return redirect(url_for('view_cart'))
    
    subtotal = 0
    for cart_item, product in cart_items:
        subtotal += product.price * cart_item.quantity
    
    delivery_fee = 5000
    total = subtotal + delivery_fee
    
    return render_template('checkout.html', 
                         cart_items=cart_items,
                         subtotal=subtotal,
                         delivery_fee=delivery_fee,
                         total=total,
                         user=user)



@app.route('/debug/admin-check')
@admin_required
def debug_admin_check():
    """Check if admin users exist"""
    admins = User.query.filter_by(user_type='admin').all()
    html = "<h1>Admin Users Check</h1>"
    html += f"<p>Total admins found: {len(admins)}</p>"
    
    if admins:
        html += "<h3>Admin List:</h3><ul>"
        for admin in admins:
            html += f"<li>ID: {admin.id} - {admin.fullname} - {admin.email}</li>"
        html += "</ul>"
    else:
        html += "<p style='color:red'>❌ No admin users found in database!</p>"
        html += "<p><a href='/create-admin' style='background:#ff6b00; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;'>Create Admin User</a></p>"
    
    return html





@app.route('/create-admin')
def create_admin():
    """Create a default admin user if none exists"""
    admin = User.query.filter_by(email='admin@shopmax.com').first()
    if not admin:
        admin = User(
            fullname='ShopMax Admin',
            email='admin@shopmax.com',
            phone='0700000000',
            password=generate_password_hash('admin123'),
            user_type='admin',
            is_active=True
        )
        db.session.add(admin)
        db.session.commit()
        return "✅ Admin user created successfully! Email: admin@shopmax.com, Password: admin123"
    return "Admin user already exists"




# ==================== MY ORDERS ROUTE (FIXED) ====================
@app.route('/my-orders')
@login_required
def my_orders():
    """Redirect to user orders page"""
    return redirect(url_for('orders'))  # ← Now points to 'orders'



# ==================== WISHLIST ROUTES ====================
@app.route('/wishlist')
@login_required
def wishlist():
    user = get_current_user()
    
    if user.user_type != 'buyer':
        flash('Wishlist is only available for buyers.', 'info')
        return redirect(url_for('products'))
    
    from sqlalchemy.orm import joinedload
    
    wishlist_items = db.session.query(Wishlist, Product).\
        join(Product, Wishlist.product_id == Product.id).\
        options(joinedload(Product.seller)).\
        filter(Wishlist.user_id == user.id).\
        order_by(Wishlist.created_at.desc()).\
        all()
    
    total_value = sum(product.price for _, product in wishlist_items)
    
    return render_template('wishlist.html', 
                         wishlist_items=wishlist_items,
                         user=user,
                         total_value=total_value)




# ============ MESSAGE API ROUTES ============

@app.route('/api/messages/unread-count', methods=['GET'])
@login_required
def api_get_unread_count():
    """Get total unread message count for user"""
    try:
        user_id = session['user_id']
        user_type = session['user_type']
        
        # Get all conversations for user
        conversations = get_user_conversations(user_id, user_type)
        
        total_unread = 0
        for conv in conversations:
            total_unread += conv.get('unread_count', 0)
        
        return jsonify({
            'success': True,
            'count': total_unread
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'count': 0,
            'error': str(e)
        })


@app.route('/api/messages/conversations/<int:conversation_id>', methods=['GET'])
@login_required
def api_get_conversation(conversation_id):
    """Get specific conversation details"""
    try:
        user_id = session['user_id']
        
        conversation = get_conversation_by_id(conversation_id, user_id)
        
        if not conversation:
            return jsonify({
                'success': False,
                'error': 'Conversation not found'
            }), 404
        
        return jsonify({
            'success': True,
            'data': conversation
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/messages/conversations/<int:conversation_id>/messages', methods=['GET'])
@login_required
def api_get_messages(conversation_id):
    """Get messages for a conversation"""
    try:
        user_id = session['user_id']
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        
        messages = get_conversation_messages(conversation_id, user_id, page, limit)
        
        # Mark messages as read
        mark_conversation_as_read(conversation_id, user_id)
        
        return jsonify({
            'success': True,
            'data': messages,
            'page': page,
            'limit': limit
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500





@app.route('/api/messages/conversations/<int:conversation_id>/read', methods=['PUT'])
@login_required
def api_mark_as_read(conversation_id):
    """Mark messages as read"""
    try:
        data = request.get_json()
        message_ids = data.get('message_ids', [])
        user_id = session['user_id']
        
        mark_messages_as_read(conversation_id, message_ids, user_id)
        
        return jsonify({
            'success': True
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/messages/<int:message_id>', methods=['DELETE'])
@login_required
def api_delete_message(message_id):
    """Delete a message"""
    try:
        user_id = session['user_id']
        
        delete_message(message_id, user_id)
        
        return jsonify({
            'success': True
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/messages/<int:message_id>', methods=['PUT'])
@login_required
def api_edit_message(message_id):
    """Edit a message"""
    try:
        data = request.get_json()
        content = data.get('content', '').strip()
        user_id = session['user_id']
        
        if not content:
            return jsonify({
                'success': False,
                'error': 'Message content cannot be empty'
            }), 400
        
        message = edit_message(message_id, user_id, content)
        
        return jsonify({
            'success': True,
            'data': message
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/messages/search', methods=['GET'])
@login_required
def api_search_messages():
    """Search messages"""
    try:
        query = request.args.get('q', '').strip()
        user_id = session['user_id']
        
        if not query:
            return jsonify({
                'success': False,
                'error': 'Search query required'
            }), 400
        
        results = search_user_messages(user_id, query)
        
        return jsonify({
            'success': True,
            'data': results
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
 






# ============ AUTH DECORATORS ============

from functools import wraps
from flask import session, redirect, url_for, jsonify

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # Check if it's an API request
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'error': 'Authentication required'
                }), 401
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function

def seller_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        
        if session.get('user_type') != 'seller':
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Seller access required'}), 403
            return redirect(url_for('home'))
            
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        
        if session.get('user_type') != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
            return redirect(url_for('home'))
            
        return f(*args, **kwargs)
    return decorated_function





 # ============ HELPER FUNCTIONS ============

def get_cart_count():
    """Get cart count for current user"""
    if 'user_id' in session:
        # Query your database for cart count
        # Return count or 0
        pass
    return 0

def get_user_conversations(user_id, user_type, page=1, limit=20):
    """Get conversations for user"""
    # Implement your database query here
    # Return list of conversations
    return []

def get_conversation_by_id(conversation_id, user_id):
    """Get specific conversation"""
    # Implement your database query
    return None

def get_conversation_messages(conversation_id, user_id, page=1, limit=50):
    """Get messages for conversation"""
    # Implement your database query
    return []

def mark_conversation_as_read(conversation_id, user_id):
    """Mark all messages in conversation as read"""
    # Implement your database update
    pass

def create_message(conversation_id, sender_id, sender_type, content, attachments=None, message_type='text'):
    """Create new message"""
    # Implement your database insert
    return {
        'id': 1,
        'content': content,
        'created_at': datetime.now()
    }

def create_conversation(participant1_id, participant1_type, participant2_id, participant2_type, conv_type, product_id=None, order_id=None):
    """Create new conversation"""
    # Implement your database insert
    return {'id': 1}

def find_existing_conversation(user1_id, user2_id, conv_type):
    """Find existing conversation between users"""
    # Implement your database query
    return None

def mark_messages_as_read(conversation_id, message_ids, user_id):
    """Mark specific messages as read"""
    # Implement your database update
    pass

def delete_message(message_id, user_id):
    """Delete a message"""
    # Implement your database delete/soft delete
    pass

def edit_message(message_id, user_id, new_content):
    """Edit a message"""
    # Implement your database update
    return {'id': message_id, 'content': new_content}

def search_user_messages(user_id, query):
    """Search user's messages"""
    # Implement your database search
    return []

def get_seller_conversations(seller_id, page=1, limit=20):
    """Get seller conversations"""
    # Implement your database query
    return []

def get_admin_conversations(admin_id, page=1, limit=20):
    """Get admin conversations"""
    # Implement your database query
    return []

def get_message_statistics():
    """Get message statistics for admin"""
    # Implement your database query
    return {
        'total_conversations': 0,
        'total_messages': 0,
        'unread_messages': 0,
        'active_today': 0
    }

def emit_new_message(conversation_id, message):
    """Emit socket event for new message"""
    # Implement socket.io emission
    pass                










# Add this custom filter for date conversion
@app.template_filter('str_to_date')
def str_to_date(date_string):
    """Convert string to date object in templates"""
    if not date_string:
        return datetime.utcnow().date()
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return datetime.utcnow().date()

        
# ==================== STATIC PAGE ROUTES ====================
@app.route('/terms-of-service')
def terms_of_service():
    """Terms of Service page"""
    return render_template('terms_of_service.html')

@app.route('/privacy-policy')
def privacy_policy():
    """Privacy Policy page"""
    return render_template('privacy_policy.html')

@app.route('/cookie-policy')
def cookie_policy():
    """Cookie Policy page"""
    return render_template('cookie_policy.html')

@app.route('/about-us')
def about_us():
    """About Us page"""
    return render_template('about_us.html')

@app.route('/contact-us')
def contact_us():
    """Contact Us page"""
    return render_template('contact_us.html')

@app.route('/help-center')
def help_center():
    """Help Center page"""
    return render_template('help_center.html')

@app.route('/faqs')
def faqs():
    """FAQs page"""
    return render_template('faqs.html')

@app.route('/how-to-buy')
def how_to_buy():
    """How to Buy page"""
    return render_template('how_to_buy.html')

@app.route('/delivery-info')
def delivery_info():
    """Delivery Information page"""
    return render_template('delivery_info.html')

@app.route('/buyer-protection')
def buyer_protection():
    """Buyer Protection page"""
    return render_template('buyer_protection.html')

@app.route('/payment-methods')
def payment_methods():
    """Payment Methods page"""
    return render_template('payment_methods.html')

@app.route('/seller-guidelines')
def seller_guidelines():
    """Seller Guidelines page"""
    return render_template('seller_guidelines.html')

@app.route('/pricing-fees')
def pricing_fees():
    """Pricing & Fees page"""
    return render_template('pricing_fees.html')

@app.route('/success-stories')
def success_stories():
    """Success Stories page"""
    return render_template('success_stories.html')

@app.route('/seller-resources')
def seller_resources():
    """Seller Resources page"""
    return render_template('seller_resources.html')


@app.route('/safety-tips')
def safety_tips():
    """Safety Tips page"""
    return render_template('safety_tips.html')

@app.route('/careers')
def careers():
    """Careers page"""
    return render_template('careers.html')




@app.route('/debug-order-status')
@admin_required
def debug_order_status():
    """Debug route to see order statuses - SEPARATE from admin dashboard"""
    from sqlalchemy import func
    
    # Get all statuses with counts
    status_counts = db.session.query(
        Order.status, 
        func.count(Order.id).label('count')
    ).group_by(Order.status).all()
    
    # Build HTML table
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Order Status Debug</title>
        <style>
            body { font-family: Arial; padding: 20px; background: #f5f5f5; }
            h1 { color: #000080; }
            table { border-collapse: collapse; width: 100%; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            th { background: #000080; color: white; padding: 12px; text-align: left; }
            td { padding: 10px; border-bottom: 1px solid #ddd; }
            .highlight { background: #fff3e0; font-weight: bold; }
            .count { color: #ff6b00; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>📊 Order Status Debug</h1>
        <table>
            <tr>
                <th>Status</th>
                <th>Count</th>
                <th>In Admin "On Way"?</th>
            </tr>
    """
    
    for status, count in status_counts:
        is_on_way = status in ['out_for_delivery', 'shipped', 'in_transit']
        row_class = 'highlight' if is_on_way else ''
        
        html += f"""
            <tr class="{row_class}">
                <td><strong>{status}</strong></td>
                <td class="count">{count}</td>
                <td>{'✅ Yes' if is_on_way else '❌ No'}</td>
            </tr>
        """
    
    # Calculate total for admin dashboard
    admin_on_way = Order.query.filter(
        db.or_(
            Order.status == 'out_for_delivery',
            Order.status == 'shipped',
            Order.status == 'in_transit'
        )
    ).count()
    
    html += f"""
        </table>
        
        <h2 style="margin-top: 30px;">Admin Dashboard Calculation:</h2>
        <p style="font-size: 18px;">
            <strong>Out for Delivery count:</strong> 
            <span style="color: #ff6b00; font-size: 24px;">{admin_on_way}</span>
        </p>
        
        <p><a href="/admin/dashboard" style="background: #000080; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">← Back to Admin Dashboard</a></p>
    </body>
    </html>
    """
    
    return html





# Add this at the bottom of app.py, just before if __name__ == '__main__':

def migrate_order_statuses():
    """Update order statuses from shipped/in_transit to out_for_delivery"""
    with app.app_context():
        print("Starting order status migration...")
        
        # Update statuses
        shipped_count = Order.query.filter_by(status='shipped').count()
        in_transit_count = Order.query.filter_by(status='in_transit').count()
        
        if shipped_count > 0:
            Order.query.filter_by(status='shipped').update({'status': 'out_for_delivery'})
            print(f"Updated {shipped_count} 'shipped' orders")
            
        if in_transit_count > 0:
            Order.query.filter_by(status='in_transit').update({'status': 'out_for_delivery'})
            print(f"Updated {in_transit_count} 'in_transit' orders")
            
        db.session.commit()
        print("✅ Migration complete!")

# Run migration on startup (temporary)
with app.app_context():
    migrate_order_statuses()




# ==================== ERROR HANDLERS ====================
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500





# ==================== FAST AI CHATBOT - NO LOGIN REQUIRED ====================

import requests
from datetime import datetime
import random

class FastAIChatbot:
    """Fast AI chatbot that works without login"""
    
    def __init__(self):
        self.ollama_url = "http://localhost:11434/api/generate"
        # Try to use faster model first
        self.model = self._get_fastest_model()
        self.ai_enabled = self._check_ollama()
        print(f"🤖 AI Status: {'Enabled' if self.ai_enabled else 'Disabled (fallback mode)'}")
        if self.ai_enabled:
            print(f"   Using model: {self.model}")
    
    def _get_fastest_model(self):
        """Get the fastest available model"""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=3)
            if response.status_code == 200:
                models = response.json().get('models', [])
                
                # Order by speed preference
                preferred = ['tinyllama', 'phi', 'mistral', 'gemma', 'llama2']
                
                for pref in preferred:
                    for model in models:
                        name = model.get('name', '')
                        if pref in name:
                            return name
                
                if models:
                    return models[0].get('name', 'tinyllama')
        except:
            pass
        return 'tinyllama'
    
    def _check_ollama(self):
        """Check if Ollama is running"""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def get_response(self, message, user_name=None):
        """Get AI response - works for all users"""
        if not user_name:
            user_name = "there"
        
        # If AI is enabled, try to get real AI response
        if self.ai_enabled:
            try:
                prompt = self._create_prompt(message, user_name)
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "num_predict": 150,  # Shorter responses = faster
                        "top_p": 0.9
                    }
                }
                
                response = requests.post(self.ollama_url, json=payload, timeout=15)
                
                if response.status_code == 200:
                    result = response.json()
                    ai_response = result.get('response', '').strip()
                    if ai_response:
                        return ai_response[:400]  # Limit length
            except Exception as e:
                print(f"AI error: {e}")
        
        # Fallback to fast rule-based responses (instant)
        return self._fast_response(message, user_name)
    
    def _create_prompt(self, message, user_name):
        """Create concise prompt for faster responses"""
        return f"""You are ShopMax AI Assistant for UCU campus marketplace. Be VERY CONCISE (2 sentences max).

Key Info:
- Delivery: Inside campus UGX 1,000-2,000, nearby UGX 2,000-3,000, town UGX 3,000-5,000
- Payment: Cash on delivery only - pay rider directly
- Seller plans: Starter FREE trial, Business UGX 30k/mo, Semester UGX 100k/4mo
- Contact: shopmax4321@gmail.com, +256 782 713764

User: {user_name}
Question: {message}

Short, helpful answer:"""
    
    def _fast_response(self, message, user_name):
        """Instant fallback responses (no AI needed)"""
        msg = message.lower().strip()
        
        # Delivery questions
        if any(w in msg for w in ['delivery', 'fee', 'cost', 'how much', 'shipping']):
            if 'campus' in msg or 'inside' in msg:
                return "🚚 Inside UCU Campus delivery: UGX 1,000-2,000. Takes 10-20 minutes. Pay the rider in cash!"
            elif 'nearby' in msg or 'bugujju' in msg or 'wandegeya' in msg:
                return "🚚 Nearby areas (Bugujju, Wandegeya): UGX 2,000-3,000. Takes 20-35 minutes. Pay rider in cash!"
            elif 'town' in msg or 'mukono' in msg:
                return "🚚 Mukono Town delivery: UGX 3,000-5,000. Takes 30-50 minutes. Pay rider in cash!"
            else:
                return "🚚 Delivery fees: Inside campus UGX 1,000-2,000, nearby UGX 2,000-3,000, town UGX 3,000-5,000. Pay rider in cash!"
        
        # Seller/Subscription questions
        elif any(w in msg for w in ['seller', 'sell', 'subscription', 'plan', 'become']):
            return "📋 To sell on ShopMax: Register as seller → choose plan (FREE 30-day trial for new sellers!) → add products. Plans: Starter FREE (4 products), Business UGX 30k/mo (25 products), Semester UGX 100k/4mo (100 products)."
        
        # Payment questions
        elif any(w in msg for w in ['pay', 'payment', 'cash', 'money', 'cod']):
            return "💰 Cash on delivery only! Pay the ShopMax rider directly when your items arrive. No online payment needed."
        
        # Order tracking
        elif any(w in msg for w in ['track', 'order', 'where', 'status']):
            return "📍 Track your order: Go to 'My Orders' → click your order → see rider's live location on the map!"
        
        # Product questions
        elif any(w in msg for w in ['product', 'buy', 'shop', 'item']):
            return "🛍️ Browse products by category or search. Add to cart → checkout with address → pay rider on delivery. Popular items: textbooks, electronics, fashion, snacks!"
        
        # Contact/Help
        elif any(w in msg for w in ['contact', 'support', 'help', 'email', 'phone']):
            return "📞 Contact ShopMax: Email shopmax4321@gmail.com, Phone +256 782 713764. Location: UCU Main Campus, ICT Building. Office hours: Mon-Fri 9AM-5PM."
        
        # Account questions
        elif any(w in msg for w in ['login', 'register', 'sign', 'account', 'create']):
            return "👤 Register with UCU email (@students.ucu.ac.ug or @ucu.ac.ug) or Google sign-in. Already have an account? Click Login!"
        
        # Safety
        elif any(w in msg for w in ['safe', 'security', 'scam']):
            return "🔒 ShopMax is safe! UCU email verification, NIN verification for sellers, cash on delivery only. Always inspect items before paying the rider!"
        
        # Greetings
        elif any(w in msg for w in ['hello', 'hi', 'hey', 'good morning', 'good afternoon']):
            greetings = [
                f"👋 Hello {user_name}! Welcome to ShopMax! How can I help you today?",
                f"Hi {user_name}! 😊 Need help with shopping, selling, or delivery?",
                f"Hey {user_name}! 👋 I'm ShopMax Assistant. Ask me anything about the UCU campus marketplace!"
            ]
            return random.choice(greetings)
        
        # Thanks
        elif any(w in msg for w in ['thank', 'thanks']):
            return f"You're welcome, {user_name}! 😊 Happy shopping on ShopMax! Anything else I can help with?"
        
        # Help
        elif 'help' in msg:
            return "🤖 I can help with:\n• Delivery fees & times\n• Seller subscriptions\n• Order tracking\n• Payment methods\n• Account help\n• Contact info\n\nWhat would you like to know?"
        
        # Default - always helpful
        else:
            return f"🤖 Hi {user_name}! I'm ShopMax Assistant. I can help with:\n• Delivery fees (UGX 1,000-5,000)\n• Becoming a seller (FREE trial!)\n• Order tracking\n• Payment (cash on delivery)\n\nWhat would you like to know?"


# Initialize AI chatbot (no login needed)
ai_chatbot = FastAIChatbot()

# Make the chatbot work WITHOUT login
@app.route('/api/chat', methods=['POST'])
def chat_api_public():
    """Public AI Chatbot - No login required!"""
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({
                'success': True,
                'response': "Hello! I'm ShopMax Assistant. What would you like to know? 😊"
            })
        
        # Get user name if logged in, otherwise use "Guest"
        if 'user_id' in session:
            user = get_current_user()
            user_name = user.fullname.split()[0] if user.fullname else 'Guest'
        else:
            user_name = 'Guest'
        
        # Get response (fast!)
        response = ai_chatbot.get_response(message, user_name)
        
        return jsonify({
            'success': True,
            'response': response
        })
        
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({
            'success': True,
            'response': "I'm here to help! Ask about delivery, becoming a seller, or tracking orders. What would you like to know?"
        })


@app.route('/api/chat/status', methods=['GET'])
def chat_status_public():
    """Check if AI is working - Public access"""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get('models', [])
            return jsonify({
                'success': True,
                'ai_enabled': True,
                'models': [m.get('name') for m in models],
                'message': 'AI is ready! 🚀'
            })
    except:
        pass
    
    return jsonify({
        'success': True,
        'ai_enabled': False,
        'message': 'AI is in fast mode (instant responses)'
    })




# ==================== MAIN ENTRY POINT ====================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
