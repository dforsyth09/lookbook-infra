-- ============================================================
-- LookBook Supabase Migration v2
-- Run this in Supabase SQL Editor to update existing tables
-- ============================================================

-- ============================================================
-- PART 1: Update clothing_items table with new ASOS fields
-- ============================================================

-- Add new columns for enhanced product data
ALTER TABLE clothing_items
ADD COLUMN IF NOT EXISTS additional_image_urls JSONB DEFAULT '[]';

ALTER TABLE clothing_items
ADD COLUMN IF NOT EXISTS colour TEXT;

ALTER TABLE clothing_items
ADD COLUMN IF NOT EXISTS is_on_sale BOOLEAN DEFAULT FALSE;

ALTER TABLE clothing_items
ADD COLUMN IF NOT EXISTS source_url TEXT;

-- Index for sale items (useful for "On Sale" filter in app)
CREATE INDEX IF NOT EXISTS idx_clothing_on_sale
ON clothing_items (is_on_sale) WHERE is_on_sale = TRUE;


-- ============================================================
-- PART 2: User accounts and linked admin system
-- ============================================================

-- Users table (both regular users and admins)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT UNIQUE,                    -- Auto-generated on first app launch
    display_name TEXT,                        -- Optional friendly name
    role TEXT NOT NULL DEFAULT 'user',        -- 'user' or 'admin'
    linked_user_id UUID REFERENCES users(id), -- For admins: which user they monitor
    created_at TIMESTAMPTZ DEFAULT now(),
    last_seen_at TIMESTAMPTZ DEFAULT now()
);

-- Index for quick device lookups
CREATE INDEX IF NOT EXISTS idx_users_device_id ON users (device_id);

-- Index for admin lookups
CREATE INDEX IF NOT EXISTS idx_users_linked ON users (linked_user_id) WHERE role = 'admin';


-- ============================================================
-- PART 3: Cart items (synced to Supabase for admin visibility)
-- ============================================================

CREATE TABLE IF NOT EXISTS cart_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES clothing_items(id) ON DELETE CASCADE,
    selected_size TEXT NOT NULL,
    added_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, product_id)  -- One entry per product per user
);

-- Index for fetching a user's cart
CREATE INDEX IF NOT EXISTS idx_cart_user ON cart_items (user_id);


-- ============================================================
-- PART 4: Wishlist items (hearts)
-- ============================================================

CREATE TABLE IF NOT EXISTS wishlist_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES clothing_items(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, product_id)
);

-- Index for fetching a user's wishlist
CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist_items (user_id);


-- ============================================================
-- PART 5: Helpful views for admin
-- ============================================================

-- View: Admin can see their linked user's cart with full product details
CREATE OR REPLACE VIEW admin_cart_view AS
SELECT
    u_admin.id AS admin_id,
    u_admin.device_id AS admin_device_id,
    u_user.id AS user_id,
    u_user.display_name AS user_name,
    ci.id AS cart_item_id,
    ci.selected_size,
    ci.added_at,
    p.id AS product_id,
    p.title,
    p.brand,
    p.colour,
    p.price,
    p.image_url,
    p.source_url,
    p.is_on_sale
FROM users u_admin
JOIN users u_user ON u_admin.linked_user_id = u_user.id
JOIN cart_items ci ON ci.user_id = u_user.id
JOIN clothing_items p ON ci.product_id = p.id
WHERE u_admin.role = 'admin';


-- ============================================================
-- PART 6: Row Level Security (RLS) Policies
-- ============================================================

-- Enable RLS on tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE cart_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE wishlist_items ENABLE ROW LEVEL SECURITY;

-- Users can read/update their own record
CREATE POLICY users_own_record ON users
    FOR ALL USING (auth.uid()::text = device_id OR auth.uid() IS NULL);

-- Admins can read their linked user's data
CREATE POLICY admin_read_linked_user ON users
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM users admin
            WHERE admin.device_id = auth.uid()::text
            AND admin.role = 'admin'
            AND admin.linked_user_id = users.id
        )
    );

-- Users can manage their own cart
CREATE POLICY cart_own_items ON cart_items
    FOR ALL USING (
        user_id IN (SELECT id FROM users WHERE device_id = auth.uid()::text)
    );

-- Admins can read their linked user's cart
CREATE POLICY cart_admin_read ON cart_items
    FOR SELECT USING (
        user_id IN (
            SELECT linked_user_id FROM users
            WHERE device_id = auth.uid()::text AND role = 'admin'
        )
    );

-- Users can manage their own wishlist
CREATE POLICY wishlist_own_items ON wishlist_items
    FOR ALL USING (
        user_id IN (SELECT id FROM users WHERE device_id = auth.uid()::text)
    );

-- Admins can read their linked user's wishlist
CREATE POLICY wishlist_admin_read ON wishlist_items
    FOR SELECT USING (
        user_id IN (
            SELECT linked_user_id FROM users
            WHERE device_id = auth.uid()::text AND role = 'admin'
        )
    );


-- ============================================================
-- PART 7: Helper function to link admin to user
-- ============================================================

-- Call this to link your admin account to her user account
-- Usage: SELECT link_admin_to_user('your-device-id', 'her-device-id');
CREATE OR REPLACE FUNCTION link_admin_to_user(admin_device TEXT, user_device TEXT)
RETURNS TEXT AS $$
DECLARE
    admin_user_id UUID;
    target_user_id UUID;
BEGIN
    -- Get or create admin user
    SELECT id INTO admin_user_id FROM users WHERE device_id = admin_device;
    IF admin_user_id IS NULL THEN
        INSERT INTO users (device_id, role, display_name)
        VALUES (admin_device, 'admin', 'Admin')
        RETURNING id INTO admin_user_id;
    ELSE
        UPDATE users SET role = 'admin' WHERE id = admin_user_id;
    END IF;

    -- Get target user
    SELECT id INTO target_user_id FROM users WHERE device_id = user_device;
    IF target_user_id IS NULL THEN
        RETURN 'Error: User with device_id ' || user_device || ' not found';
    END IF;

    -- Link them
    UPDATE users SET linked_user_id = target_user_id WHERE id = admin_user_id;

    RETURN 'Success: Admin ' || admin_device || ' linked to user ' || user_device;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
