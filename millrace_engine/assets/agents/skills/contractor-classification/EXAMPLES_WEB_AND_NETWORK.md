# Examples - Web and Networked Business Systems

---

## EX-CONT-201: Church CRM

**Tags**: `network_application`, `crud_business_system`, `church_ops`

**Prompt**:
Build church CRM software for pastors with households, attendance, notes, follow-up reminders, and workflow automation.

**Good classification**:
- `shape_class = network_application`
- `archetype = crud_business_system`
- `host_platform = church_ops`
- `stack_hints = []` until explicit
- `specificity_level = L3`

**Why**:
This is a networked business system built around records, workflows, and likely role-based access.

---

## EX-CONT-202: Support ticket app

**Tags**: `network_application`, `crud_business_system`, `support_operations`

**Prompt**:
Build the first usable support-ticket web app for a Python service.

**Good classification**:
- `shape_class = network_application`
- `archetype = crud_business_system`
- `host_platform = support_operations`
- `stack_hints = []` unless the prompt or repo actually supports more
- `specificity_level = L3`

**Why**:
The user-visible product is a web-based workflow system.

---

## EX-CONT-203: Internal analytics dashboard

**Tags**: `network_application`, `dashboard_portal`

**Prompt**:
Create an internal dashboard to visualize tenant usage, error rates, weekly model burn, and queue health.

**Good classification**:
- `shape_class = network_application`
- `archetype = dashboard_portal`
- `specificity_level = L2`

**Why**:
The core product is an interactive networked dashboard, even if the data comes from services.

---

## EX-CONT-204: Realtime collaboration app

**Tags**: `network_application`, `interactive_application`, `realtime`

**Prompt**:
Build a collaborative browser whiteboard with presence, shared cursors, and low-latency updates.

**Good classification**:
- `shape_class = network_application`
- `archetype = collaborative_workspace`
- `specializations = {"delivery": "realtime"}` if the realtime requirement is explicit
- `specificity_level = L3` or `L5`

**Why**:
Network coordination is fundamental here.

---

## EX-CONT-205: JSON API backend only

**Tags**: `service_backend`, `api_service`

**Prompt**:
Build a backend service that exposes an authenticated JSON API for invoice generation and payment reconciliation.

**Good classification**:
- `shape_class = service_backend`
- `archetype = api_service`
- `specializations = {"auth": "required"}` if explicit
- `specificity_level = L2` or `L5`

**Why**:
This is not a user-facing network application; it is primarily a backend service.
