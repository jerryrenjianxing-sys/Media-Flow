# evidence-governance Specification

## Purpose
TBD - created by archiving change add-review-and-evidence-governance. Update Purpose after archive.

## Requirements

### Requirement: Evidence inventory is read-only

The system SHALL report file count, total bytes, oldest evidence, newest evidence, and database size for approved local evidence roots without modifying those files.

#### Scenario: Reviewer opens governance page

- **WHEN** the local governance page requests evidence status
- **THEN** the API SHALL return a current read-only inventory

### Requirement: Retention policy does not delete evidence

The system SHALL store a positive retention-days policy and MUST always expose auto_delete as false.

#### Scenario: Retention days are saved

- **WHEN** a reviewer saves a valid retention period
- **THEN** the system SHALL record the policy without scheduling or performing deletion

### Requirement: Backups are non-destructive

The system SHALL create a new SQLite backup and JSON inventory manifest under the local backup directory without overwriting runtime data.

#### Scenario: Reviewer creates a backup

- **WHEN** the reviewer requests a database backup
- **THEN** a new timestamped backup SHALL be created and the live database SHALL remain available

### Requirement: Evidence images use path allowlists

The API SHALL only serve review images resolved beneath configured evidence roots.

#### Scenario: Image path is outside approved roots

- **WHEN** a record or request resolves outside all approved roots
- **THEN** the API SHALL reject the image request
