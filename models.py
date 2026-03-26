from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    location = db.Column(db.String(100), nullable=True)
    password = db.Column(db.String(200), nullable=True)
    user_type = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    # Buyer specific fields
    delivery_address = db.Column(db.Text, nullable=True)
    
    # Seller specific fields
    
    business_name = db.Column(db.String(100), nullable=True)
    business_address = db.Column(db.Text)
    nin = db.Column(db.String(50))
    subscription_tier = db.Column(db.String(20), default='basic')
    subscription_expiry = db.Column(db.DateTime)


    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    products = db.relationship('Product', backref='seller', lazy=True)
    orders = db.relationship('Order', backref='user', lazy=True)
    order_items = db.relationship('OrderItem', backref='seller', lazy=True)
    wishlists = db.relationship('Wishlist', backref='user', lazy=True)
    carts = db.relationship('Cart', backref='user', lazy=True)
    reviews = db.relationship('Review', backref='user', lazy=True)

    def set_password(self, password):
        self.password = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password, password)
    
    def get_id(self):
        return str(self.id)
    
    def __repr__(self):
        return f'<User {self.email}>'

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
    
    # Additional fields
    brand = db.Column(db.String(50), nullable=True)
    condition = db.Column(db.String(20), default='new', nullable=True)
    weight = db.Column(db.Float, nullable=True)
    dimensions = db.Column(db.String(50), nullable=True)
    tags = db.Column(db.String(200), nullable=True)
    
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
    
    def __repr__(self):
        return f'<Product {self.name}>'
    
class Order(db.Model):
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False)  # ADD THIS
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, processing, shipped, delivered, cancelled
    delivery_address = db.Column(db.Text)
    payment_method = db.Column(db.String(50))
    payment_status = db.Column(db.String(20), default='pending')  # pending, paid, failed, refunded
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # CRITICAL: Add delivery_confirmed field (this is what shows in admin dashboard)
    delivery_confirmed = db.Column(db.Boolean, default=False)  # Customer confirmation
    delivered_at = db.Column(db.DateTime)  # When admin marked as delivered
    
    # Delivery proof and notes
    delivery_proof = db.Column(db.String(255))
    delivery_notes = db.Column(db.Text)
    delivery_confirmed_at = db.Column(db.DateTime)  # When customer confirmed
    delivery_confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    # Rating and feedback
    buyer_rating = db.Column(db.Integer)
    buyer_feedback = db.Column(db.Text)
    
    # Issue reporting
    issues_reported = db.Column(db.Boolean, default=False)
    issue_description = db.Column(db.Text)
    
    # Timestamps (REMOVE DUPLICATES - keep only one set)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    paid_at = db.Column(db.DateTime)  # ADD THIS for payment timestamp
    
    # Relationships
    order_items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    tracking_updates = db.relationship('OrderTracking', backref='order', lazy=True, cascade='all, delete-orphan')
    delivery_assignments = db.relationship('DeliveryAssignment', backref='order', lazy=True, cascade='all, delete-orphan')
    delivery_confirmation = db.relationship('DeliveryConfirmation', backref='order', uselist=False, cascade='all, delete-orphan')
    
    # User relationships
    user = db.relationship('User', backref='orders', foreign_keys=[user_id])
    delivery_confirmer = db.relationship('User', foreign_keys=[delivery_confirmed_by])
    
    def __repr__(self):
        return f'<Order {self.order_number if hasattr(self, "order_number") else self.id}>'
    
    # Helper method to check if delivery is confirmed
    def is_delivery_confirmed(self):
        return self.delivery_confirmed and self.delivery_confirmed_at is not None
    
    # Generate order number if not present
    def generate_order_number(self):
        if not self.order_number:
            from datetime import datetime
            timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
            self.order_number = f'ORD-{timestamp}-{self.id:06d}'
        return self.order_number       
   




class DeliveryConfirmation(db.Model):
    __tablename__ = 'delivery_confirmations'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), unique=True, nullable=False)
    confirmed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    confirmed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    proof_image = db.Column(db.String(255))  # Optional: Image proof
    customer_notes = db.Column(db.Text)
    rating = db.Column(db.Integer)  # 1-5 stars
    feedback = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    confirmed_by = db.relationship('User', backref='delivery_confirmations')
    
    def __repr__(self):
        return f'<DeliveryConfirmation for Order {self.order_id}>'




class OrderItem(db.Model):
    __tablename__ = 'order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    
    # Foreign keys
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    def __repr__(self):
        return f'<OrderItem {self.id}>'

class Wishlist(db.Model):
    __tablename__ = 'wishlists'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Wishlist {self.id}>'

class Cart(db.Model):
    __tablename__ = 'carts'
    
    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, default=1)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Cart {self.id}>'

class Review(db.Model):
    __tablename__ = 'reviews'
    
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Review {self.id}>'

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
    
    def __repr__(self):
        return f'<SubscriptionPlan {self.name}>'

class OrderTracking(db.Model):
    __tablename__ = 'order_tracking'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    location = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<OrderTracking {self.id}>'




class DeliveryPerson(db.Model):
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False, unique=True)
    vehicle_type = db.Column(db.String(50))  # boda, car, bicycle, etc.
    vehicle_number = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    deliveries = db.relationship('Delivery', backref='delivery_person', lazy=True)

class Delivery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    delivery_person_id = db.Column(db.Integer, db.ForeignKey('delivery_person.id'), nullable=False)
    status = db.Column(db.String(20), default='assigned')  # assigned, picked_up, in_transit, delivered
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    picked_up_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    delivery_notes = db.Column(db.Text)
    
    # GPS Tracking
    current_latitude = db.Column(db.Float)
    current_longitude = db.Column(db.Float)
    last_updated = db.Column(db.DateTime)

class DeliveryTracking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey('delivery.id'), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    location_name = db.Column(db.String(200))  # Human-readable location