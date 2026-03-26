// Admin Dashboard JavaScript
document.addEventListener('DOMContentLoaded', function() {
    // Initialize charts
    initCharts();
    
    // Initialize real-time updates
    initRealTimeUpdates();
    
    // Initialize search and filter functionality
    initSearchFilter();
    
    // Initialize modal handlers
    initModals();
});

// Chart initialization
function initCharts() {
    // Sales Chart
    const salesCtx = document.getElementById('salesChart');
    if (salesCtx) {
        const salesChart = new Chart(salesCtx, {
            type: 'line',
            data: {
                labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'],
                datasets: [{
                    label: 'Sales (₦)',
                    data: [120000, 190000, 300000, 500000, 200000, 300000],
                    borderColor: '#4e73df',
                    backgroundColor: 'rgba(78, 115, 223, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return `₦${context.parsed.y.toLocaleString()}`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return '₦' + value.toLocaleString();
                            }
                        }
                    }
                }
            }
        });
    }

    // User Distribution Chart
    const userCtx = document.getElementById('userDistributionChart');
    if (userCtx) {
        const userChart = new Chart(userCtx, {
            type: 'doughnut',
            data: {
                labels: ['Sellers', 'Buyers', 'Admins'],
                datasets: [{
                    data: [totalSellers, totalBuyers, totalAdmins || 1],
                    backgroundColor: ['#1cc88a', '#36b9cc', '#4e73df'],
                    hoverBackgroundColor: ['#17a673', '#2c9faf', '#2e59d9'],
                    borderWidth: 2,
                    borderColor: '#fff'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            usePointStyle: true
                        }
                    }
                }
            }
        });
    }
}

// Real-time updates
function initRealTimeUpdates() {
    // Update stats every 30 seconds
    setInterval(updateDashboardStats, 30000);
    
    // Real-time notifications
    initWebSocket();
}

function updateDashboardStats() {
    fetch('/admin/api/stats')
        .then(response => response.json())
        .then(data => {
            // Update stats cards
            document.getElementById('totalUsers').textContent = data.total_users;
            document.getElementById('totalSellers').textContent = data.total_sellers;
            document.getElementById('totalBuyers').textContent = data.total_buyers;
            document.getElementById('totalProducts').textContent = data.total_products;
            document.getElementById('totalOrders').textContent = data.total_orders;
            document.getElementById('totalSales').textContent = formatCurrency(data.total_sales);
            
            // Show update notification
            showNotification('Stats updated', 'success');
        })
        .catch(error => {
            console.error('Error updating stats:', error);
        });
}

// Search and filter functionality
function initSearchFilter() {
    const searchInput = document.getElementById('tableSearch');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            const searchTerm = e.target.value.toLowerCase();
            const table = document.querySelector('.admin-table');
            const rows = table.querySelectorAll('tbody tr');
            
            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                row.style.display = text.includes(searchTerm) ? '' : 'none';
            });
        });
    }
    
    // Filter buttons
    const filterButtons = document.querySelectorAll('.filter-btn');
    filterButtons.forEach(button => {
        button.addEventListener('click', function() {
            const filter = this.getAttribute('data-filter');
            filterTable(filter);
            
            // Update active state
            filterButtons.forEach(btn => btn.classList.remove('active'));
            this.classList.add('active');
        });
    });
}

function filterTable(filter) {
    const rows = document.querySelectorAll('.admin-table tbody tr');
    
    rows.forEach(row => {
        if (filter === 'all') {
            row.style.display = '';
        } else {
            const userType = row.querySelector('.user-type').textContent.toLowerCase();
            row.style.display = userType.includes(filter) ? '' : 'none';
        }
    });
}

// Modal handlers
function initModals() {
    // User detail modals
    const userDetailButtons = document.querySelectorAll('.user-detail-btn');
    userDetailButtons.forEach(button => {
        button.addEventListener('click', function() {
            const userId = this.getAttribute('data-user-id');
            showUserDetails(userId);
        });
    });
    
    // Close modals
    const closeButtons = document.querySelectorAll('.close-modal');
    closeButtons.forEach(button => {
        button.addEventListener('click', function() {
            this.closest('.modal').style.display = 'none';
        });
    });
}

function showUserDetails(userId) {
    fetch(`/admin/api/users/${userId}`)
        .then(response => response.json())
        .then(user => {
            // Populate modal with user data
            document.getElementById('userDetailName').textContent = user.fullname;
            document.getElementById('userDetailEmail').textContent = user.email;
            document.getElementById('userDetailPhone').textContent = user.phone || 'N/A';
            document.getElementById('userDetailType').textContent = user.user_type;
            document.getElementById('userDetailBusiness').textContent = user.business_name || 'N/A';
            document.getElementById('userDetailCreated').textContent = new Date(user.created_at).toLocaleDateString();
            
            // Show modal
            document.getElementById('userDetailModal').style.display = 'block';
        })
        .catch(error => {
            console.error('Error fetching user details:', error);
            showNotification('Error loading user details', 'error');
        });
}


// In admin_orders.html or your main JS file
document.addEventListener('DOMContentLoaded', function() {
    const resolveButtons = document.querySelectorAll('.resolve-issue');
    const resolveForm = document.getElementById('resolveIssueForm');
    
    resolveButtons.forEach(button => {
        button.addEventListener('click', function() {
            const orderId = this.getAttribute('data-order-id');
            resolveForm.action = `/admin/orders/${orderId}/resolve-issue`;
        });
    });
});




// Utility functions
function formatCurrency(amount) {
    return '₦' + parseFloat(amount).toLocaleString('en-NG', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-dismissible fade show`;
    notification.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    const container = document.getElementById('notificationContainer');
    container.appendChild(notification);
    
    // Auto remove after 5 seconds
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

// WebSocket for real-time updates (simplified)
function initWebSocket() {
    // In a real application, you would connect to a WebSocket server
    console.log('WebSocket initialized for real-time updates');
    
    // Simulate real-time notifications
    setInterval(() => {
        const activities = [
            'New user registered',
            'New order placed',
            'Product added',
            'Payment received'
        ];
        const randomActivity = activities[Math.floor(Math.random() * activities.length)];
        
        // Only show occasional notifications
        if (Math.random() > 0.7) {
            showNotification(randomActivity, 'info');
        }
    }, 60000); // Every minute
}

// Export functions for global access
window.adminDashboard = {
    refreshStats: updateDashboardStats,
    showUserDetails: showUserDetails,
    formatCurrency: formatCurrency
};