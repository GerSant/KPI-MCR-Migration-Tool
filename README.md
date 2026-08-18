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
   git clone https://github.com/your-username/mcr-migration-tool.git
   cd mcr-migration-tool
   ```

2. **Create and activate a virtual environment (optional but recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Linux/macOS
   # venv\Scripts\activate   # On Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install requests
   ```

---

## ⚙️ Configuration (`params.conf`)

Create or edit the `params.conf` file in the project root directory with the following structure:

```ini
[CREDENTIALS]
client_id = YOUR_CLIENT_ID
client_secret = YOUR_CLIENT_SECRET

[SETTINGS]
environment = staging
# Set 'true' to prompt for each VXC individually, or 'false' to migrate all in sequence
prompt_per_vxc = false

[ENDPOINTS_PRODUCTION]
auth_url = https://auth.megaport.com/oauth2/token
base_url = https://api.megaport.com

[ENDPOINTS_STAGING]
auth_url = https://auth-staging.megaport.com/oauth2/token
base_url = https://api-staging.megaport.com
```

---

## 📖 Usage

Run the script by passing the exact name of the source MCR you wish to migrate as an argument:

```bash
python mcr-migration-tool.py "SOURCE_MCR_NAME"
```

### Execution Flow

1. **Inspection:** Finds the MCR, validates lock statuses, and parses its parameters, sub-resources, and connected VXCs.
2. **Export:** Automatically generates a backup configuration file named `<MCR_NAME>_export.json`.
3. **User Input:** Prompts for the new MCR name and target speed based on the location's supported capabilities.
4. **Phase 1:** Validates, places the order, and polls until the new MCR is provisioned.
5. **Phase 2:** Applies TAGs, Flow Exports, Prefix/Packet Filters, and the IPsec Add-on to the new MCR.
6. **Phase 3:** Re-points connected VXCs to the new MCR's UUID based on your prompt preferences.