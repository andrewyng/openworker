//! Optional private operator bridge. No inbound ports, public IP or workload authority.
use super::*;
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Access {
    pub mode: String,
    pub ami_id: String,
}
impl Access {
    pub(super) fn validate(&self) -> Result<()> {
        ensure!(
            self.mode == "ssm"
                && self
                    .ami_id
                    .strip_prefix("ami-")
                    .is_some_and(|s| s.len() == 17
                        && s.bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))),
            "Pin an Amazon ARM64 AMI for optional SSM access"
        );
        Ok(())
    }
}
pub(super) fn infrastructure(p: &Plan, r: &mut BTreeMap<String, Value>, outputs: &mut Value) {
    let Some(access) = &p.infrastructure.as_ref().unwrap().operator_access else {
        return;
    };
    r.insert("OperatorSecurityGroup".into(),resource("AWS::EC2::SecurityGroup",json!({"GroupDescription":"SSM operator bridge: outbound HTTPS only, no ingress","VpcId":reference("Vpc"),"SecurityGroupEgress":[{"IpProtocol":"tcp","FromPort":443,"ToPort":443,"CidrIp":"0.0.0.0/0"}],"Tags":tags(p)})));
    r.get_mut("ManagementApiSecurityGroup").unwrap()["Properties"]["SecurityGroupIngress"]
        .as_array_mut().unwrap().push(json!({"IpProtocol":"tcp","FromPort":443,"ToPort":443,"SourceSecurityGroupId":reference("OperatorSecurityGroup")}));
    let mut instance = resource(
        "AWS::EC2::Instance",
        json!({"ImageId":access.ami_id,"InstanceType":"t4g.micro","SubnetId":reference("PrivateSubnet0"),"SecurityGroupIds":[reference("OperatorSecurityGroup")],"IamInstanceProfile":format!("{}-access",p.prefix()),"MetadataOptions":{"HttpTokens":"required","HttpPutResponseHopLimit":1},"BlockDeviceMappings":[{"DeviceName":"/dev/xvda","Ebs":{"Encrypted":true,"VolumeSize":8,"VolumeType":"gp3","DeleteOnTermination":true}}],"PropagateTagsToVolumeOnCreation":true,"Tags":tags(p)}),
    );
    instance["DependsOn"] = json!(["NatRoute", "PrivateRouteAssociation0"]);
    r.insert("OperatorAccess".into(), instance);
    outputs["OperatorInstanceId"] = json!({"Value":reference("OperatorAccess")});
}
pub(super) fn bootstrap(p: &Plan, r: &mut serde_json::Map<String, Value>) {
    if p.infrastructure.as_ref().unwrap().operator_access.is_none() {
        return;
    }
    r.insert("OperatorRole".into(),resource("AWS::IAM::Role",json!({"RoleName":format!("{}-access",p.prefix()),"AssumeRolePolicyDocument":service_trust("ec2.amazonaws.com"),"Policies":[{"PolicyName":"session-channels-only","PolicyDocument":agent_policy(p)}],"Tags":tags(p)})));
    r.insert("OperatorProfile".into(),resource("AWS::IAM::InstanceProfile",json!({"InstanceProfileName":format!("{}-access",p.prefix()),"Roles":[reference("OperatorRole")]})));
}
pub(super) fn agent_policy(p: &Plan) -> Value {
    json!({"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssm:UpdateInstanceInformation","ssmmessages:CreateControlChannel","ssmmessages:CreateDataChannel","ssmmessages:OpenControlChannel","ssmmessages:OpenDataChannel"],"Resource":"*","Condition":{"StringEquals":{"aws:RequestedRegion":scope(p).1}}}]})
}
pub(super) fn permissions(p: &Plan) -> Value {
    let (account, region) = scope(p);
    let prefix = p.prefix();
    let id = &p.deployment_id;
    let ec2 = |kind: &str| format!("arn:aws:ec2:{region}:{account}:{kind}/*");
    json!({"Version":"2012-10-17","Statement":[
        {"Sid":"LaunchTaggedOperator","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("instance"),"Condition":{"StringEquals":{"aws:RequestTag/ows:deployment-id":id,"aws:RequestedRegion":region,"ec2:InstanceType":"t4g.micro"},"Null":{"ec2:LaunchTemplate":"true"}}},
        {"Sid":"NewOperatorDisk","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("volume"),"Condition":{"StringEquals":{"aws:RequestTag/ows:deployment-id":id,"aws:RequestedRegion":region},"Null":{"ec2:LaunchTemplate":"true"}}},
        {"Sid":"OwnedOperatorParents","Effect":"Allow","Action":"ec2:RunInstances","Resource":[ec2("subnet"),ec2("security-group")],"Condition":{"StringEquals":{"ec2:ResourceTag/ows:deployment-id":id}}},
        {"Sid":"OperatorNetworkInterface","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("network-interface"),"Condition":{"StringEquals":{"aws:RequestedRegion":region}}},
        {"Sid":"PinnedOperatorAmi","Effect":"Allow","Action":"ec2:RunInstances","Resource":format!("arn:aws:ec2:{region}::image/{}",p.infrastructure.as_ref().unwrap().operator_access.as_ref().unwrap().ami_id)},
        {"Sid":"TagNewOperator","Effect":"Allow","Action":"ec2:CreateTags","Resource":[ec2("instance"),ec2("volume")],"Condition":{"StringEquals":{"aws:RequestTag/ows:deployment-id":id,"ec2:CreateAction":"RunInstances"}}},
        {"Sid":"ManageOwnedOperator","Effect":"Allow","Action":["ec2:TerminateInstances","ec2:CreateTags"],"Resource":ec2("instance"),"Condition":{"StringEquals":{"ec2:ResourceTag/ows:deployment-id":id},"StringEqualsIfExists":{"aws:RequestTag/ows:deployment-id":id}}},
        {"Sid":"ObserveOperator","Effect":"Allow","Action":["ec2:DescribeInstances","ec2:DescribeInstanceStatus","ec2:DescribeInstanceAttribute","ec2:DescribeInstanceCreditSpecifications","ec2:DescribeImages","ec2:DescribeVolumes","ec2:DescribeIamInstanceProfileAssociations"],"Resource":"*","Condition":{"StringEquals":{"aws:RequestedRegion":region}}},
        {"Sid":"ReadOperatorProfile","Effect":"Allow","Action":"iam:GetInstanceProfile","Resource":format!("arn:aws:iam::{account}:instance-profile/{prefix}-access")},
        {"Sid":"PassOnlyOperatorRole","Effect":"Allow","Action":"iam:PassRole","Resource":role_arn(p,"access"),"Condition":{"StringEquals":{"iam:PassedToService":"ec2.amazonaws.com"}}}
    ]})
}
