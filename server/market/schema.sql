-- AionDex shared market samples (see src/index.js)
CREATE TABLE IF NOT EXISTS samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- grows on every change: clients ask for what came after their last id
  market TEXT NOT NULL,                   -- "<server>-<side>", e.g. 2102-asmodian
  author TEXT NOT NULL,                   -- hash of the sender's random install id
  at REAL NOT NULL,                       -- when the sample was taken (unix time)
  key TEXT NOT NULL,                      -- the sample's public id (a hash of author + at)
  profession TEXT,
  tax REAL,
  received INTEGER NOT NULL,
  version TEXT,                           -- AionDex version that sent it
  data TEXT                               -- the items as JSON; NULL = deleted by its author
);
CREATE UNIQUE INDEX IF NOT EXISTS samples_author_at ON samples (author, at);
CREATE INDEX IF NOT EXISTS samples_market_id ON samples (market, id);
