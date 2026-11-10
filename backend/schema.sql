CREATE TABLE incidents (
	id VARCHAR(36) NOT NULL, 
	reference VARCHAR(24) NOT NULL, 
	incident_type VARCHAR(40) NOT NULL, 
	severity VARCHAR(12) NOT NULL, 
	severity_reasons JSONB NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	order_id VARCHAR(80) NOT NULL, 
	capture_id VARCHAR(80), 
	title VARCHAR(200) NOT NULL, 
	amount NUMERIC(12, 2), 
	currency VARCHAR(8) NOT NULL, 
	is_synthetic BOOLEAN NOT NULL, 
	scenario VARCHAR(40), 
	outcome_certainty VARCHAR(16), 
	authoritative_state VARCHAR(24), 
	local_state VARCHAR(24), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	resolved_by VARCHAR(80), 
	resolution_note TEXT, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_incidents_incident_type ON incidents (incident_type);
CREATE INDEX ix_incidents_order_id ON incidents (order_id);
CREATE INDEX ix_incidents_status ON incidents (status);
CREATE INDEX ix_incidents_severity ON incidents (severity);
CREATE INDEX ix_incidents_created_at ON incidents (created_at);
CREATE UNIQUE INDEX ix_incidents_reference ON incidents (reference);

CREATE TABLE orders (
	id VARCHAR(36) NOT NULL, 
	order_ref VARCHAR(80) NOT NULL, 
	capture_id VARCHAR(80), 
	amount NUMERIC(12, 2), 
	currency VARCHAR(8) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	local_state VARCHAR(24), 
	is_synthetic BOOLEAN NOT NULL, 
	scenario VARCHAR(40), 
	capture_request_id VARCHAR(64), 
	processed_capture_ids JSONB NOT NULL, 
	payment_processing_count INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_orders_order_ref ON orders (order_ref);
CREATE INDEX ix_orders_capture_id ON orders (capture_id);

CREATE TABLE audit_logs (
	id VARCHAR(36) NOT NULL, 
	incident_id VARCHAR(36), 
	order_ref VARCHAR(80), 
	actor VARCHAR(80) NOT NULL, 
	action VARCHAR(60) NOT NULL, 
	details_redacted JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(incident_id) REFERENCES incidents (id)
);
CREATE INDEX ix_audit_logs_order_ref ON audit_logs (order_ref);
CREATE INDEX ix_audit_logs_incident_id ON audit_logs (incident_id);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE TABLE investigations (
	id VARCHAR(36) NOT NULL, 
	incident_id VARCHAR(36) NOT NULL, 
	summary TEXT NOT NULL, 
	root_cause TEXT NOT NULL, 
	supporting_evidence JSONB NOT NULL, 
	contradictory_evidence JSONB NOT NULL, 
	alternative_explanations JSONB NOT NULL, 
	confidence VARCHAR(16) NOT NULL, 
	evidence_confidence VARCHAR(16) NOT NULL, 
	llm_confidence VARCHAR(16), 
	recommended_action JSONB NOT NULL, 
	prohibited_actions JSONB NOT NULL, 
	missing_information JSONB NOT NULL, 
	generated_by VARCHAR(80) NOT NULL, 
	validation JSONB NOT NULL, 
	safety JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(incident_id) REFERENCES incidents (id)
);
CREATE INDEX ix_investigations_created_at ON investigations (created_at);
CREATE INDEX ix_investigations_incident_id ON investigations (incident_id);

CREATE TABLE transaction_events (
	id VARCHAR(36) NOT NULL, 
	incident_id VARCHAR(36), 
	order_ref VARCHAR(80) NOT NULL, 
	capture_ref VARCHAR(80), 
	source VARCHAR(24) NOT NULL, 
	event_type VARCHAR(32) NOT NULL, 
	external_event_id VARCHAR(120), 
	timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
	http_status INTEGER, 
	error_code VARCHAR(80), 
	transaction_state VARCHAR(24), 
	correlation_id VARCHAR(120), 
	verified BOOLEAN NOT NULL, 
	is_synthetic BOOLEAN NOT NULL, 
	delivery_count INTEGER NOT NULL, 
	last_delivery_at TIMESTAMP WITH TIME ZONE, 
	payload_redacted JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(incident_id) REFERENCES incidents (id), 
	UNIQUE (external_event_id)
);
CREATE INDEX ix_transaction_events_order_ref ON transaction_events (order_ref);
CREATE INDEX ix_transaction_events_capture_ref ON transaction_events (capture_ref);
CREATE INDEX ix_transaction_events_incident_id ON transaction_events (incident_id);
CREATE INDEX ix_transaction_events_timestamp ON transaction_events (timestamp);

