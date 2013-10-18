-- add verified to lates
ALTER TABLE late ADD COLUMN verified BOOLEAN DEFAULT FALSE;
