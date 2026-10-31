# LNMU Result Updater - Complete Upgrade Plan

## Current System Analysis
- Flask app with OTP login, rollno lookup, result fetching, SQL updates
- Telegram bot for notifications (single admin chat)
- SQLite DB for student lookup
- SQL Runner for batch updates via LNMU web APIs

## Required Upgrades

### 1. Remove Password/OTP Authentication
- Replace time-based password with admin-only access
- Remove `verify_password()` and OTP logic
- Implement simple session-based auth for admin

### 2. User Request Submission Flow
- User submits rollno + semester
- Request goes to admin queue (not direct update)
- Payment QR auto-generated (UPI: buypremiumcontent@ybl)
- User submits payment screenshot
- Admin reviews request with reason
- 24-hour cooldown after rejection before resubmission

### 3. Admin Approval System
- Admin dashboard with pending requests
- Approve/Reject with reason
- On approval: execute SQL updates
- On rejection: notify user with reason

### 4. Payment System
- UPI QR code generation
- Payment screenshot upload
- Payment verification (manual or OCR)
- Amount: configurable

### 5. Broadcast Channel
- Telegram channel for status updates
- Join button for users
- Rollno masking in broadcasts
- Status notifications (approve/reject)

### 6. Database Schema
New tables: requests, request_history, broadcast_subscribers, audit_log

## Implementation Order
1. Database schema changes
2. Remove password/OTP auth
3. Request submission API
4. Admin approval API
5. Payment QR generation
6. Broadcast channel integration
7. Frontend updates
8. Testing