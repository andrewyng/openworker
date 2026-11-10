//! EKS validates tagged custom launch templates using the caller's EC2 authority.
//! Keep launch permission separate from network management, and authorize each
//! RunInstances resource independently. No generic EC2 launch or IAM mutation.
use super::*;
pub(super) fn permissions(p: &Plan) -> Value {
    let (account, region) = scope(p);
    let ec2 = |kind: &str| format!("arn:aws:ec2:{region}:{account}:{kind}/*");
    let template = ec2("launch-template");
    let via_template = json!({"ArnLike":{"ec2:LaunchTemplate":template},"StringEquals":{"aws:RequestedRegion":region}});
    let id = &p.deployment_id;
    json!({"Version":"2012-10-17","Statement":[
        {"Sid":"TaggedWorkerInstances","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("instance"),"Condition":{"StringEquals":{"aws:RequestedRegion":region,"aws:RequestTag/ows:deployment-id":id,"ec2:InstanceType":"t4g.large"},"ArnLike":{"ec2:LaunchTemplate":template}}},
        {"Sid":"TaggedWorkerDisks","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("volume"),"Condition":{"StringEquals":{"aws:RequestedRegion":region,"aws:RequestTag/ows:deployment-id":id},"ArnLike":{"ec2:LaunchTemplate":template}}},
        {"Sid":"OwnedWorkerTemplate","Effect":"Allow","Action":"ec2:RunInstances","Resource":template,"Condition":{"StringEquals":{"ec2:ResourceTag/ows:deployment-id":id}}},
        {"Sid":"OwnedWorkerSubnets","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("subnet"),"Condition":{"StringEquals":{"ec2:ResourceTag/ows:deployment-id":id},"ArnLike":{"ec2:LaunchTemplate":template}}},
        {"Sid":"OwnedWorkerSecurityGroup","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("security-group"),"Condition":{"StringEquals":{"ec2:ResourceTag/ows:deployment-id":id},"ArnLike":{"ec2:LaunchTemplate":template}}},
        {"Sid":"WorkerAmiFromTemplate","Effect":"Allow","Action":"ec2:RunInstances","Resource":format!("arn:aws:ec2:{region}::image/*"),"Condition":via_template},
        {"Sid":"NewWorkerInterface","Effect":"Allow","Action":"ec2:RunInstances","Resource":ec2("network-interface"),"Condition":via_template},
        {"Sid":"TagNewWorkers","Effect":"Allow","Action":"ec2:CreateTags","Resource":[ec2("instance"),ec2("volume")],"Condition":{"StringEquals":{"aws:RequestTag/ows:deployment-id":id,"ec2:CreateAction":"RunInstances"}}}
    ]})
}
