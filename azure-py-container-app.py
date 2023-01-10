import pulumi
import pulumi_azure as azure
import pulumi_azure_native as azure_native
from pulumi_azure_native import resources, network, containerinstance
from pulumi_azure_native.containerinstance import ContainerGroupNetworkProfileArgs, EnvironmentVariableArgs, \
    ImageRegistryCredentialArgs, VolumeArgs
from pulumi_azure_native.network import DelegationArgs

config = pulumi.Config()
registry_name = config.get("registryName", "registry")
registry_admin_username = config.get("registryAdminUsername", "user")
registry_admin_password = config.get("registryAdminPassword", "pass")
agent_image_tag = config.get("agentImageTag", "latest")

rg_devops = resources.get_resource_group("rg1")
rg_development = resources.get_resource_group("rg2")

tags = {
    "CreatedBy": "Pulumi",
    "PulumiProject": pulumi.get_project(),
    "PulumiStack": pulumi.get_stack(),
    "PulumiOrg": pulumi.get_organization(),
    "Env": "Dev"
}

workspace = operationalinsights.Workspace("loganalytics",
                                          resource_group_name=rg_devops.name,
                                          sku=operationalinsights.WorkspaceSkuArgs(name="PerGB2018"),
                                          retention_in_days=30,
                                          tags=tags)

workspace_shared_keys = pulumi.Output.all(rg_devops.name, workspace.name).apply(
    lambda args: operationalinsights.get_shared_keys(
        resource_group_name=args[0],
        workspace_name=args[1]
    ))
    
azure_devops_service_tags = azure.network.get_service_tags(location=rg_devops.location,
                                                           service="AzureDevOps")

devops_nsg = azure.network.NetworkSecurityGroup("devops-nsg",
                                                location=rg_devops.location,
                                                resource_group_name=rg_devops.name,
                                                security_rules=[azure.network.NetworkSecurityGroupSecurityRuleArgs(
                                                    name="AllowAzureDevopsIn",
                                                    description="Allow all access from AzureDevops instances.",
                                                    priority=110,
                                                    direction="Inbound",
                                                    access="Allow",
                                                    protocol="Tcp",
                                                    source_port_range="*",
                                                    destination_port_range="*",
                                                    source_address_prefixes=azure_devops_service_tags.address_prefixes,
                                                    destination_address_prefix="*",
                                                ), azure.network.NetworkSecurityGroupSecurityRuleArgs(
                                                    name="AllowAzureDevopsOut",
                                                    description="Allow all access to AzureDevops instances.",
                                                    priority=110,
                                                    direction="Outbound",
                                                    access="Allow",
                                                    protocol="Tcp",
                                                    source_port_range="*",
                                                    destination_port_range="*",
                                                    source_address_prefix="*",
                                                    destination_address_prefixes=azure_devops_service_tags.address_prefixes,
                                                )
                                                ],
                                                tags=tags)

dev_vnet = azure.network.get_virtual_network(name='vnet-development',
                                             resource_group_name=rg_development.name)

devops_vnet = azure.network.VirtualNetwork('devops-vnet',
                                           resource_group_name=rg_devops.name,
                                           location=rg_devops.location,
                                           address_spaces=["172.20.0.0/16"],
                                           tags=tags)

devops_subnet = network.Subnet('snet-devops',
                               resource_group_name=rg_devops.name,
                               virtual_network_name=devops_vnet.name,
                               address_prefix="172.20.112.0/20",
                               delegations=[
                                   DelegationArgs(name='snet-delegation-containergroups',
                                                  service_name='Microsoft.ContainerInstance/containerGroups')])

devops_subnet_nsg_association = azure.network.SubnetNetworkSecurityGroupAssociation(
    "devops_subnet_nsg_association",
    subnet_id=devops_subnet.id,
    network_security_group_id=devops_nsg.id)

example_1_vnet_peering = azure.network.VirtualNetworkPeering("devops-dev-p",
                                                             resource_group_name=rg_devops.name,
                                                             virtual_network_name=devops_vnet.name,
                                                             remote_virtual_network_id=dev_vnet.id)

example_2_vnet_peering = azure.network.VirtualNetworkPeering("dev-devops-p",
                                                             resource_group_name=rg_development.name,
                                                             virtual_network_name=dev_vnet.name,
                                                             remote_virtual_network_id=devops_vnet.id)

storage_account = storage.StorageAccount("devopssa",
                                         kind="StorageV2",
                                         location=rg_devops.location,
                                         resource_group_name=rg_devops.name,
                                         sku=storage.SkuArgs(
                                             name="Standard_LRS",
                                         ), tags=tags)

file_share = storage.FileShare("file-share",
                               account_name=storage_account.name,
                               enabled_protocols="SMB",
                               resource_group_name=rg_devops.name,
                               share_quota=5)

managed_env = app.ManagedEnvironment("azure-agents-env",
                                     resource_group_name=rg_devops.name,
                                     vnet_configuration=VnetConfigurationArgs(
                                         infrastructure_subnet_id=devops_subnet.id
                                     ),
                                     app_logs_configuration=app.AppLogsConfigurationArgs(
                                         destination="log-analytics",
                                         log_analytics_configuration=app.LogAnalyticsConfigurationArgs(
                                             customer_id=workspace.customer_id,
                                             shared_key=workspace_shared_keys.apply(lambda r: r.primary_shared_key)
                                         )),
                                     tags=tags)

account_key = pulumi.Output.all(rg_devops.name, storage_account.name) \
    .apply(lambda args: storage.list_storage_account_keys(
    resource_group_name=args[0],
    account_name=args[1]
)).apply(lambda account_keys: account_keys.keys[0].value)

storage_name = 'devops-storagemount'
managed_environments_storage = app.ManagedEnvironmentsStorage("managedEnvironmentsStorage",
                                                              environment_name=managed_env.name,
                                                              properties=app.ManagedEnvironmentStoragePropertiesArgs(
                                                                  azure_file=app.AzureFilePropertiesArgs(
                                                                      access_mode="ReadWrite",
                                                                      account_key=account_key,
                                                                      account_name=storage_account.name,
                                                                      share_name=file_share.name,
                                                                  ),
                                                              ),
                                                              resource_group_name=rg_devops.name,
                                                              storage_name=storage_name)


container_app = app.ContainerApp("azure-agent-app",
                                 resource_group_name=rg_devops.name,
                                 managed_environment_id=managed_env.id,
                                 configuration=app.ConfigurationArgs(
                                     registries=[
                                         app.RegistryCredentialsArgs(
                                             server=registry_name,
                                             username=registry_admin_username,
                                             password_secret_ref="pwd")
                                     ],
                                     secrets=[
                                         app.SecretArgs(
                                             name="pwd",
                                             value=registry_admin_password),
                                         app.SecretArgs(
                                             name="az-pat",
                                             value="testpat")
                                     ],
                                 ),
                                 template=app.TemplateArgs(
                                     containers=[
                                         app.ContainerArgs(
                                             name="azure-agent",
                                             image=image_name,
                                             env=[EnvironmentVarArgs(name="AZP_POOL", value="Containers"),
                                                  EnvironmentVarArgs(name="AZP_TOKEN",
                                                                     secret_ref="az-pat"),
                                                  EnvironmentVarArgs(name="AZP_URL",
                                                                     value="https://dev.azure.com/example-organiztion")
                                                  ],
                                             resources=ContainerResourcesArgs(cpu=2,
                                                                              memory='4.0Gi'),
                                             volume_mounts=[
                                                 VolumeMountArgs(volume_name='workspace', mount_path='/azp/_work')
                                             ]
                                         )
                                     ],
                                     volumes=[
                                         VolumeArgs(name='workspace', storage_name=storage_name,
                                                    storage_type=StorageType.AZURE_FILE)
                                     ],
                                     scale=ScaleArgs(max_replicas=1, min_replicas=1)),
                                 tags=tags)
