#! /usr/bin/env python3
import argparse
import configparser
import os
import sys
import time
from copy import deepcopy
from botocore.exceptions import ClientError
import boto3


def parse_arguments(arguments):
    parser = argparse.ArgumentParser(
        description='Deploy service with one or more tasks'
    )
    parser.add_argument('-r', '--region')
    parser.add_argument('-p', '--profile', default=None)
    parser.add_argument('-c', '--cluster', default='default')
    parser.add_argument('-s', '--service-name', dest='service')
    parser.add_argument('-d', '--task-definition', nargs=1, dest='taskName')
    parser.add_argument('-k', '--copy-images', dest='copy')
    parser.add_argument('-u', '--update', action='store_true')
    parser.add_argument('-i', '--image', nargs=2, action='append')
    parser.add_argument('-t', '--timeout', default=90, type=int)
    parser.add_argument('-b', '--backoff', default=5, type=int)
    parser.add_argument('-O', '--only-if-modified', action='store_true',
                        dest='onlyIfModified')

    return parser.parse_args(arguments)


def get_aws_region(aws_profile):
    region = None
    if 'AWS_DEFAULT_REGION' in os.environ:
        region = os.environ['AWS_DEFAULT_REGION']
    else:
        try:
            with open(os.path.expanduser('~/.aws/config'), 'r') as config_file:
                config = configparser.RawConfigParser()
                config.read_file(config_file)
                region = config.get('profile ' + aws_profile, 'region') \
                    if aws_profile else config.get('default', 'region')
        except FileNotFoundError:
            pass

    return region


def get_task_arn(ecs, cluster, service):
    task_arn = None
    try:
        result = ecs.describe_services(cluster=cluster, services=(service, ))
        task_arn = result['services'][0]['taskDefinition']
    except IndexError:
        print('Service {} not found.'.format(service))
    except ClientError as exc:
        if exc.response['Error']['Message'] == 'Cluster not found.':
            print('Cluster {} not fund.'.format(cluster))
        else:
            raise exc

    return task_arn


def wait_for_task(ecs, cluster, service, new_task_def_arn, timeout, backoff):
    time_start = time.time()
    while True:
        time.sleep(backoff)

        result = ecs.list_tasks(cluster=cluster, serviceName=service,
                                desiredStatus='RUNNING')
        wait_text = 'New task is not running yet'

        if result and 'taskArns' in result:
            try:
                tasks = ecs.describe_tasks(cluster=cluster,
                                           tasks=result['taskArns'])
                if tasks and 'tasks' in tasks:
                    has_updated = [
                        x for x in tasks['tasks']
                        if x['taskDefinitionArn'] == new_task_def_arn
                    ]
                    if len(has_updated) > 0:
                        return True
            except ClientError as exc:
                if (exc.response['Error']['Message'] ==
                        'Tasks cannot be empty.'):
                    wait_text = 'No tasks are currently running'
                else:
                    raise exc

        if time.time() - time_start > timeout:
            return False

        print(wait_text + ', backing off for {} seconds.'.format(backoff))


def main():
    args = parse_arguments(sys.argv[1:])

    region = args.region if args.region else get_aws_region(args.profile)
    if not region:
        print('Unable to identify default AWS region.\n'
              'You need to specify a region using either '
              '-r (--region), by setting AWS_DEFAULT_REGION variable '
              'or by specifying it in ~/.aws/config file.')
        sys.exit(1)

    # Naive "rules" implementation
    if args.update and args.taskName:
        print("update and task-definition does not work together")
        sys.exit(1)
    elif args.copy and not args.update:
        print("Implicitly enabling update since you're using copy.")
        args.update = True
    elif not args.update and not args.taskName and not args.copy:
        print("You need to specify a task-definition "
              "or to copy using --copy-images")
        sys.exit(1)

    # create boto3 session with ecs client
    session = boto3.Session(region_name=region, profile_name=args.profile)
    ecs = session.client('ecs')

    cluster = args.cluster
    service = args.service
    task_arn = get_task_arn(ecs, cluster, service) if args.update else None
    if not task_arn:
        print("Unable to locate ARN for task.")
        sys.exit(1)

    images = deepcopy(args.image)
    if args.copy:
        images = []
        result = ecs.describe_services(cluster=cluster, services=(args.copy, ))
        task = ecs.describe_task_definition(
            taskDefinition=result['services'][0]['taskDefinition']
        )
        for container in task['taskDefinition']['containerDefinitions']:
            images.append((container["name"], container["image"]))
        if len(images) > 0:
            print("Found and copied these container images:")
            print("\n".join(["{}: {}".format(*image) for image in images]))
        else:
            print("No container images found to copy, sorry.")
            sys.exit(1)

    task = ecs.describe_task_definition(taskDefinition=task_arn)

    new_task_def = dict(
        family=task['taskDefinition']['family'],
        volumes=deepcopy(task['taskDefinition']['volumes']),
        containerDefinitions=deepcopy(
            task['taskDefinition']['containerDefinitions']
        ),
    )

    has_update = False
    if images:
        for image in images:
            container = [x for x in new_task_def["containerDefinitions"]
                         if x["name"] == image[0]]
            if container and container[0]["image"] != image[1]:
                has_update = True
                container[0]["image"] = image[1]

    if args.onlyIfModified and not has_update:
        print("No container images was updated, aborting")
        sys.exit(0)

    result = ecs.register_task_definition(**new_task_def)
    new_task_def_arn = result['taskDefinition']['taskDefinitionArn']
    print("New task definition: {}".format(new_task_def_arn))

    # update service
    if not args.service:
        print("Successfully updated task definition.")
        sys.exit(0)
    else:
        ecs.update_service(cluster=cluster, service=service,
                           taskDefinition=new_task_def_arn)

    # wait for task to be running
    if wait_for_task(ecs, cluster, service, new_task_def_arn,
                     args.timeout, args.backoff):
        print('Service updated successfully, new task definition is running.')
        sys.exit(0)
    else:
        print('ERROR: New task definition is not running '
              'within {} second(s)...'.format(args.timeout))
        sys.exit(1)


if __name__ == "__main__":
    main()
