"""Create (or reuse) the dynamic group and policy the VM's instance principal needs.

Run from a machine with the operator's API key (~/.oci/config) and the OCI Python SDK:

    .venv/bin/python deploy/instance_principals.py --public-ip 147.224.201.81

It finds the running instance with that public IP in the tenancy root, then makes sure
of two things, reusing either if it exists:

- dynamic group `fitcheck-instances`, matching rule `instance.id = '<that instance>'`;
- tenancy policy `fitcheck-instances-ai` with the two statements below.

No OCID is written anywhere; the tenancy comes from the config file. The script only
prints names and states, and never prints a key or an OCID.
"""

from __future__ import annotations

import argparse
import sys

DYNAMIC_GROUP = "fitcheck-instances"
POLICY = "fitcheck-instances-ai"
STATEMENTS = [
    f"Allow dynamic-group {DYNAMIC_GROUP} to use generative-ai-family in tenancy",
    f"Allow dynamic-group {DYNAMIC_GROUP} to use ai-service-language-family in tenancy",
]


def find_instance(oci, config: dict, public_ip: str):
    tenancy = config["tenancy"]
    compute = oci.core.ComputeClient(config)
    network = oci.core.VirtualNetworkClient(config)
    instances = oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment_id=tenancy
    ).data
    for instance in instances:
        if instance.lifecycle_state != "RUNNING":
            continue
        attachments = compute.list_vnic_attachments(
            compartment_id=tenancy, instance_id=instance.id
        ).data
        for attachment in attachments:
            if network.get_vnic(attachment.vnic_id).data.public_ip == public_ip:
                return instance
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--public-ip", required=True)
    parser.add_argument("--profile", default="DEFAULT")
    args = parser.parse_args(argv)

    import oci

    config = oci.config.from_file(profile_name=args.profile)
    tenancy = config["tenancy"]
    identity = oci.identity.IdentityClient(config)

    instance = find_instance(oci, config, args.public_ip)
    if instance is None:
        print(f"no running instance has public IP {args.public_ip}", file=sys.stderr)
        return 1
    print(f"instance: {instance.display_name}")
    rule = f"instance.id = '{instance.id}'"

    groups = oci.pagination.list_call_get_all_results(
        identity.list_dynamic_groups, compartment_id=tenancy
    ).data
    group = next((g for g in groups if g.name == DYNAMIC_GROUP), None)
    if group is None:
        group = identity.create_dynamic_group(
            oci.identity.models.CreateDynamicGroupDetails(
                compartment_id=tenancy,
                name=DYNAMIC_GROUP,
                matching_rule=rule,
                description="Fit-Check web VM, for instance-principal calls to "
                "Generative AI and Language",
            )
        ).data
        print(f"dynamic group {DYNAMIC_GROUP}: created ({group.lifecycle_state})")
    elif (identity.get_dynamic_group(group.id).data.matching_rule or "").strip() != rule:
        # The list call leaves matching_rule empty on identity-domain tenancies.
        identity.update_dynamic_group(
            group.id, oci.identity.models.UpdateDynamicGroupDetails(matching_rule=rule)
        )
        print(f"dynamic group {DYNAMIC_GROUP}: reused, matching rule updated to this instance")
    else:
        print(f"dynamic group {DYNAMIC_GROUP}: reused ({group.lifecycle_state})")

    policies = oci.pagination.list_call_get_all_results(
        identity.list_policies, compartment_id=tenancy
    ).data
    policy = next((p for p in policies if p.name == POLICY), None)
    if policy is None:
        policy = identity.create_policy(
            oci.identity.models.CreatePolicyDetails(
                compartment_id=tenancy,
                name=POLICY,
                statements=STATEMENTS,
                description="Fit-Check VM may call Generative AI and Language",
            )
        ).data
        print(f"policy {POLICY}: created ({policy.lifecycle_state})")
    else:
        missing = [s for s in STATEMENTS if s not in policy.statements]
        if missing:
            identity.update_policy(
                policy.id,
                oci.identity.models.UpdatePolicyDetails(
                    statements=list(policy.statements) + missing
                ),
            )
            print(f"policy {POLICY}: reused, added {len(missing)} statement(s)")
        else:
            print(f"policy {POLICY}: reused ({policy.lifecycle_state})")
    for statement in STATEMENTS:
        print(f"  {statement}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
