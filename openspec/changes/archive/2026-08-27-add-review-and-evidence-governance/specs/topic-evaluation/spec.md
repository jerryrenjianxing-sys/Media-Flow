# topic-evaluation Delta

## ADDED Requirements

### Requirement: Human review is the formal ground truth

The system SHALL keep candidate labels, RiskFlow decisions, and human-confirmed labels as separate fields. Formal agreement metrics MUST include only samples with a human-confirmed label.

#### Scenario: Pending sample is excluded

- **WHEN** a seeded sample has candidate and RiskFlow labels but no human confirmation
- **THEN** it SHALL appear as pending review and SHALL NOT enter the formal agreement denominator

#### Scenario: Human confirms a label

- **WHEN** an authorized local reviewer confirms exact, adjacent, unrelated, or uncertain
- **THEN** the system SHALL persist the human label and use it for formal evaluation

### Requirement: Coverage gaps stay visible

The system SHALL report missing relevance classes and missing hard-negative tags, including keyword-only and visual-commerce.

#### Scenario: Dataset lacks a boundary class

- **WHEN** no confirmed sample covers a required class or hard-negative tag
- **THEN** the result SHALL remain provisional and identify the missing coverage
