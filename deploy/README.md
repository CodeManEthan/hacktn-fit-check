# Deploying to OCI

The app runs on the Compute instance and talks to MySQL HeatWave over the VCN's
private subnet. HeatWave is not reachable from outside the VCN, so the app must
run on the instance — there is no "point your laptop at the cloud database"
option.

## What exists already

| Resource | Address |
|---|---|
| Compute `Webserver`, VM.Standard.E5.Flex | 149.130.217.66 public, 10.0.0.198 private |
| MySQL HeatWave `Hackathon MySQL HeatWave` | 10.0.1.106:3306, private subnet |
| VCN `mysql_vcn` | 10.0.0.0/16, public + private subnets |

## Steps

1. **Open the port.** In the OCI console: Networking → VCN `mysql_vcn` →
   `mysql_public_subnet` → its security list → add an ingress rule, source
   `0.0.0.0/0`, TCP destination port `8000`.

2. **Create the database.** From the instance:
   ```
   mysql -h 10.0.1.106 -u <admin> -p -e "CREATE DATABASE IF NOT EXISTS fitcheck;"
   ```

3. **Write `/opt/fitcheck/.env`:**
   ```
   DATABASE_URL=mysql+pymysql://<admin>:<password>@10.0.1.106:3306/fitcheck
   OCI_REGION=us-chicago-1
   REASONING_MODEL=meta.llama-3.3-70b-instruct
   VISION_MODEL=meta.llama-3.2-90b-vision-instruct
   ```

4. **Run the bootstrap:**
   ```
   curl -fsSL https://raw.githubusercontent.com/SakxamShrestha/hacktn-fit-check/main/deploy/bootstrap.sh | bash
   ```

## Generative AI from the instance

Prefer **instance principals** over copying the API key onto the server. Create a
dynamic group matching the instance and a policy:

```
allow dynamic-group fitcheck-instances to use generative-ai-family in tenancy
```

Then set `OCI_AUTH=instance_principal` and no key file is needed. Until that
exists, the instance needs `~/.oci/config` and the private key, which is the
less safe option.
