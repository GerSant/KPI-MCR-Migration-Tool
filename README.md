# Megaport MCR Migration Tool

A Python tool designed to automate the inspection, JSON export, re-deployment, and migration of Megaport Cloud Routers (MCR), including all sub-resources and associated VXC connections.

---

## 🚀 Features

* **Lock Validation:** Automatically detects if the source MCR or any connected VXCs have an active lock flag before initiating the process.
* **Dynamic Speed Querying:** Fetches supported MCR speeds directly from the API (`/v3/locations`) for the target data center.
* **3-Phase Execution Workflow:**
  * **Phase 1 (MCR Deployment):** Handles order validation, purchase, and status polling until readiness (`LIVE` / `DOWN`).
  * **Phase 2 (Sub-resources & Attributes):** Automatically replicates Resource TAGs, Flow Exports, Prefix Filter Lists, Packet Filter Lists, and the IPsec Add-on.
  * **Phase 3 (VXC Migration):** Updates connected VXC endpoints to target the newly deployed MCR (`PUT /v3/product/vxc/{vxcUid}`).
* **Flexible Prompting:** Configure via settings whether to migrate all VXCs sequentially with a single prompt or confirm each VXC migration individually.

---

## 📋 Prerequisites

* **Python:** v3.8 or higher.
* **External Libraries:** `requests`

---

## 🛠️ Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/your-username/mcr-migration-tool.git](https://github.com/your-username/mcr-migration-tool.git)
   cd mcr-migration-tool
2. Create and activate a virtual environment (optional but recommended):
```bash
   python3 -m venv venv
   source venv/bin/activate  # On Linux/macOS
   # venv\Scripts\activate   # On Windows
3. Install dependencies:
```bash
   pip install requests
