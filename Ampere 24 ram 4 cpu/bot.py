import oci
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 1. Validate ALL required environment variables up front.
#    Failing fast with a clear message beats a cryptic SDK stack trace.
# ---------------------------------------------------------------------------
REQUIRED_VARS = [
    "OCI_USER_ID",
    "OCI_PRIVATE_KEY",
    "OCI_FINGERPRINT",
    "OCI_TENANCY_ID",
    "OCI_REGION",
    "OCI_SUBNET_ID",
    "OCI_IMAGE_ID",
    "OCI_PUBLIC_SSH_KEY",
]

missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
if missing:
    print(f"CRITICAL ERROR: Missing/empty required secrets: {', '.join(missing)}")
    sys.exit(1)

config = {
    "user": os.getenv("OCI_USER_ID"),
    "key_content": os.getenv("OCI_PRIVATE_KEY"),
    "fingerprint": os.getenv("OCI_FINGERPRINT"),
    "tenancy": os.getenv("OCI_TENANCY_ID"),
    "region": os.getenv("OCI_REGION"),
}

try:
    compute_client = oci.core.ComputeClient(config)
    print("OCI Authentication Successful. Initializing loop sequence...")
except Exception as e:
    print(f"Authentication Failed: {e}")
    sys.exit(1)

# Execution parameters
compartment_id = os.getenv("OCI_TENANCY_ID")
subnet_id = os.getenv("OCI_SUBNET_ID")
image_id = os.getenv("OCI_IMAGE_ID")
public_ssh_key = os.getenv("OCI_PUBLIC_SSH_KEY").strip()

DISPLAY_NAME = "FX-Backend-Server"

# Availability Domains to cycle through
ads = ["uufj:PHX-AD-1", "uufj:PHX-AD-2", "uufj:PHX-AD-3"]

# NOTE: keep total runtime comfortably under the cron interval (5 min) so
# scheduled runs don't overlap. 4 attempts * 60s sleep ~= 4 min worst case.
# Override with OCI_MAX_ATTEMPTS / OCI_RETRY_DELAY_SECONDS if you change the
# cron schedule in oci_spawn.yml.
total_attempts = int(os.getenv("OCI_MAX_ATTEMPTS", "4"))
retry_delay_seconds = int(os.getenv("OCI_RETRY_DELAY_SECONDS", "60"))


def instance_already_exists() -> bool:
    """
    Guard against launching a duplicate instance. This matters because the
    GitHub Actions schedule can occasionally overlap runs (see concurrency
    note in oci_spawn.yml) — this check is a second line of defense so we
    never launch two Ampere A1 instances and blow past the Always Free quota.
    """
    try:
        instances = oci.pagination.list_call_get_all_results(
            compute_client.list_instances,
            compartment_id=compartment_id,
            display_name=DISPLAY_NAME,
        ).data
        active = [
            i for i in instances
            if i.lifecycle_state in ("PROVISIONING", "RUNNING", "STARTING")
        ]
        return len(active) > 0
    except oci.exceptions.ServiceError as e:
        # If we can't verify, fail safe by NOT blocking the run — but log it
        # loudly so it's visible in the Actions log.
        print(f"-> WARNING: Could not check for existing instances ({e.message}). Proceeding cautiously.")
        return False


if instance_already_exists():
    print(f"Instance '{DISPLAY_NAME}' already exists and is active. Nothing to do. Exiting.")
    sys.exit(0)

for i in range(1, total_attempts + 1):
    current_ad = ads[(i - 1) % len(ads)]
    print(f"[Attempt {i}/{total_attempts}] Requesting instance in {current_ad}...")

    try:
        request = oci.core.models.LaunchInstanceDetails(
            display_name=DISPLAY_NAME,
            compartment_id=compartment_id,
            availability_domain=current_ad,
            shape="VM.Standard.A1.Flex",
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=4,
                memory_in_gbs=24
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=image_id,
                boot_volume_size_in_gbs=100
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id,
                assign_public_ip=True,
                assign_private_dns_record=True,
                display_name="forexalertsvnic"
            ),
            metadata={
                "ssh_authorized_keys": public_ssh_key
            }
        )

        response = compute_client.launch_instance(request)
        if response.status == 200:
            print("SUCCESS! Instance creation initialized. Exiting.")
            sys.exit(0)

    except oci.exceptions.ServiceError as e:
        if "Out of host capacity" in str(e) or e.status in (500, 429):
            print(f"-> Capacity/rate-limit issue ({e.status}). Resting {retry_delay_seconds}s...")
        else:
            print(f"-> API Error [{e.status}]: {e.message}")
    except (oci.exceptions.ConnectTimeout, oci.exceptions.RequestException) as e:
        print(f"-> Network/transport error: {e}. Resting {retry_delay_seconds}s...")
    except Exception as e:
        # Catch-all so an unexpected error doesn't burn the remaining
        # attempts of this run silently.
        print(f"-> Unexpected error: {e}. Resting {retry_delay_seconds}s...")

    if i < total_attempts:
        time.sleep(retry_delay_seconds)

print("Attempts exhausted for this run. The next scheduled run will retry.")
sys.exit(0)
