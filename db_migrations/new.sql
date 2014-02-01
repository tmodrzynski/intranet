ALTER TABLE "sprint" ADD COLUMN "board" TEXT NOT NULL DEFAULT '';
CREATE TABLE sprint_board (
    id SERIAL NOT NULL, 
    board TEXT NOT NULL, 
    name TEXT NOT NULL, 
    user_id INTEGER, 
    PRIMARY KEY (id), 
    UNIQUE (name), 
    FOREIGN KEY(user_id) REFERENCES "user" (id)
)
