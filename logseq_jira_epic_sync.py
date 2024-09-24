#!/usr/bin/env python

import sys
import os
import json
import re  # Regular expressions for link conversion
import time  # For sleep functionality
import logging
import configparser
import argparse
from jira import JIRA
from dotenv import load_dotenv  # For loading .env file

class Node:
    def __init__(self, indent, line):
        self.indent = indent
        self.line = ''
        self.children = []
        self.parent = None
        self.type = None  # Epic, Task, Sub-task
        self.status = None  # TODO, DOING, DONE
        self.description_lines = []  # Stores tuples of (indent, line)
        self.relations = []
        self.jira_issue = None
        self.path = ''
        self.description_text = ''  # Stores the final description text

def convert_markdown_links_to_jira(text):
    # Regular expression pattern to find markdown links: [text](URL)
    pattern = r'\[([^\]]+)\]\(([^\)]+)\)'
    replacement = r'[\1|\2]'
    return re.sub(pattern, replacement, text)

def build_description_text(description_lines):
    if not description_lines:
        return ''
    # Find the minimum indentation level among description lines
    min_indent = min(indent for indent, _ in description_lines)
    # Adjust lines to have relative indentation and convert to Jira nested list syntax
    adjusted_lines = []
    for indent, line in description_lines:
        relative_indent = (indent - min_indent) // 4  # Assuming 4 spaces per level
        # Use multiple '*' for nested lists in Jira
        bullet = '*' * (relative_indent + 1)
        adjusted_line = f"{bullet} {line}"
        adjusted_lines.append(adjusted_line)
    return '\n'.join(adjusted_lines)

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Create or update Jira issues from input file.')
    parser.add_argument('input_file', nargs='?', default='input.txt', help='Path to the input file.')
    parser.add_argument('--config', default='/root/config.ini', help='Path to the configuration file.')
    args = parser.parse_args()

    input_file = args.input_file
    config_file = args.config

    # Load configuration
    config = configparser.ConfigParser()
    config.read(config_file)

    # Get settings from the configuration file
    issue_mapping_file = config.get('Settings', 'issue_mapping_file', fallback='issue_mapping.json')
    log_file = config.get('Settings', 'log_file', fallback='script.log')

    # Set up logging
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    while True:
        # Load environment variables from .env file
        load_dotenv()

        # Get Jira credentials and project key from environment variables
        jira_server = os.environ.get('JIRA_SERVER')
        jira_user = os.environ.get('JIRA_USER')
        jira_token = os.environ.get('JIRA_TOKEN')
        project_key = os.environ.get('JIRA_PROJECT_KEY')

        if not all([jira_server, jira_user, jira_token, project_key]):
            logging.error("Jira credentials and project key must be set in the .env file.")
            sys.exit(1)

        options = {'server': jira_server}
        try:
            jira = JIRA(options, basic_auth=(jira_user, jira_token))
        except Exception as e:
            logging.error(f"Failed to connect to Jira: {e}")
            sys.exit(1)

        # Get the authenticated user's account ID
        try:
            current_user = jira.myself()
            account_id = current_user['accountId']
        except Exception as e:
            logging.error(f"Failed to get current user info from Jira: {e}")
            sys.exit(1)

        # Load issue mapping from file
        if os.path.exists(issue_mapping_file):
            try:
                with open(issue_mapping_file, 'r') as f:
                    issue_mapping = json.load(f)
            except Exception as e:
                logging.error(f"Error reading issue mapping file '{issue_mapping_file}': {e}")
                issue_mapping = {}
        else:
            issue_mapping = {}

        try:
            with open(input_file, 'r') as f:
                lines = f.readlines()
        except Exception as e:
            logging.error(f"Error reading input file '{input_file}': {e}")
            time.sleep(300)
            continue

        # Process lines
        in_logbook = False
        stack = []
        root_nodes = []
        current_node = None
        current_indent = 0

        for idx, line in enumerate(lines):
            # Remove the newline at the end and expand tabs
            line = line.rstrip('\n').expandtabs(4)

            # Skip if line is empty
            if not line.strip():
                continue

            # Handle :LOGBOOK: and :END:
            if ':LOGBOOK:' in line:
                in_logbook = True
                continue
            if ':END:' in line:
                in_logbook = False
                continue
            if in_logbook:
                continue

            # Determine the indentation level
            indent = len(line) - len(line.lstrip())
            content = line.strip()

            # Check if line starts with '- '
            if content.startswith('- '):
                # Determine if it's a new node, description, or relation
                node_content = content[2:].strip()
                # Check for status
                status = None
                if node_content.startswith(('TODO', 'DOING', 'DONE')):
                    status_and_rest = node_content.split(' ', 1)
                    status = status_and_rest[0]
                    if len(status_and_rest) > 1:
                        node_line = status_and_rest[1].strip()
                    else:
                        node_line = ''
                    # It's a new node
                    node = Node(indent, node_line)
                    node.status = status
                    node.line = node_line

                    # Determine parent node
                    while stack and indent <= stack[-1].indent:
                        stack.pop()
                    if stack:
                        parent = stack[-1]
                        node.parent = parent
                        parent.children.append(node)
                    else:
                        root_nodes.append(node)
                    stack.append(node)
                    current_node = node
                    current_indent = indent

                    # Check for levels beyond sub-task
                    level = len(stack)
                    if level > 3:
                        logging.error("Tickets beyond sub-task detected. Exiting.")
                        sys.exit(1)
                elif node_content.startswith('#'):
                    # It's a relation
                    if current_node:
                        current_node.relations.append(node_content)
                    else:
                        logging.warning(f"Relation '{node_content}' found with no parent node.")
                else:
                    # It's part of the description of the current node
                    if current_node:
                        description_line = node_content
                        current_node.description_lines.append((indent, description_line))
                    else:
                        # No current node, ignore
                        pass
            else:
                # Line does not start with '- ', it could be description or relation
                if content.startswith('#'):
                    # It's a relation
                    if current_node:
                        current_node.relations.append(content)
                    else:
                        logging.warning(f"Relation '{content}' found with no parent node.")
                else:
                    # It's a description line
                    if current_node:
                        current_node.description_lines.append((indent, content))
                    else:
                        # No current node, ignore
                        pass

        # Assign types to nodes and compute paths
        def assign_types_and_paths(node, level=1, path=''):
            if level == 1:
                node.type = 'Epic'
            elif level == 2:
                node.type = 'Task'
            elif level == 3:
                node.type = 'Sub-task'
            node.path = path + '/' + node.line
            # Build the description text here
            node.description_text = build_description_text(node.description_lines)
            node.description_text = convert_markdown_links_to_jira(node.description_text)
            for child in node.children:
                assign_types_and_paths(child, level+1, node.path)

        for root in root_nodes:
            assign_types_and_paths(root)

        # Map custom statuses to Jira statuses
        status_mapping = {
            'TODO': 'Backlog',
            'DOING': 'In Progress',
            'DONE': 'Done'
        }

        # Now, create or update the issues in Jira
        def create_or_update_issue(node):
            # Check if issue exists in mapping
            issue_key = issue_mapping.get(node.path)
            if issue_key:
                # Issue exists, update it
                try:
                    issue = jira.issue(issue_key)
                    node.jira_issue = issue
                    logging.info(f"Processing {node.type} '{node.line}' with key {issue.key}")

                    # Retrieve current description and status
                    current_description = issue.fields.description or ''
                    current_status = issue.fields.status.name

                    # Compare descriptions
                    if current_description.strip() != node.description_text.strip():
                        # Update description
                        issue.update(fields={'description': node.description_text})
                        logging.info(f"Updated description for {issue.key}")

                    # Map custom status to Jira status
                    if node.status and node.status in status_mapping:
                        jira_status = status_mapping[node.status]
                        if current_status != jira_status:
                            # Transition issue to new status
                            transitions = jira.transitions(issue)
                            transition_id = None
                            for t in transitions:
                                if t['name'] == jira_status:
                                    transition_id = t['id']
                                    break
                            if transition_id:
                                jira.transition_issue(issue, transition_id)
                                logging.info(f"Updated status of {issue.key} to {jira_status}")
                    # Update assignee if necessary
                    if issue.fields.assignee is None or issue.fields.assignee.accountId != account_id:
                        issue.update(fields={'assignee': {'id': account_id}})
                        logging.info(f"Assigned {issue.key} to current user")

                except Exception as e:
                    logging.error(f"Error updating issue {issue_key}: {e}")
                    # Remove from mapping and recreate
                    del issue_mapping[node.path]
                    create_or_update_issue(node)
                    return
            else:
                # Issue does not exist, create it
                issue_dict = {
                    'project': {'key': project_key},
                    'summary': node.line,
                    'description': node.description_text,
                    'issuetype': {'name': node.type},
                    'assignee': {'id': account_id},  # Assign to authenticated user
                }

                # For Epics, set the 'Epic Name' field (adjust customfield ID as per your Jira instance)
                if node.type == 'Epic':
                    # Adjust the custom field ID as per your Jira instance
                    epic_name_field = 'customfield_10011'  # Replace with your 'Epic Name' field ID
                    issue_dict[epic_name_field] = node.line

                # For Sub-tasks, set the parent
                if node.type == 'Sub-task' and node.parent:
                    issue_dict['parent'] = {'key': node.parent.jira_issue.key}

                # For Tasks, set Epic Link if parent is an Epic
                if node.type == 'Task' and node.parent and node.parent.type == 'Epic':
                    # Adjust the custom field ID as per your Jira instance
                    epic_link_field = 'customfield_10014'  # Replace with your 'Epic Link' field ID
                    issue_dict[epic_link_field] = node.parent.jira_issue.key

                # Create the issue
                try:
                    issue = jira.create_issue(fields=issue_dict)
                    node.jira_issue = issue
                    logging.info(f"Created {node.type} '{node.line}' with key {issue.key}")
                    # Add to mapping
                    issue_mapping[node.path] = issue.key
                except Exception as e:
                    logging.error(f"Error creating issue '{node.line}': {e}")
                    return

                # Set status if needed
                if node.status and node.status in status_mapping:
                    jira_status = status_mapping[node.status]
                    transitions = jira.transitions(issue)
                    transition_id = None
                    for t in transitions:
                        if t['name'] == jira_status:
                            transition_id = t['id']
                            break
                    if transition_id:
                        try:
                            jira.transition_issue(issue, transition_id)
                            logging.info(f"Set status of {issue.key} to {jira_status}")
                        except Exception as e:
                            logging.error(f"Error setting status for issue {issue.key}: {e}")

            # Handle relations
            for relation in node.relations:
                # Implement relation handling
                logging.info(f"Handling relation '{relation}' for issue {node.jira_issue.key}")
                # Extract issue keys from relation
                related_issue_keys = re.findall(r'#([A-Z]+-\d+)', relation)
                for key in related_issue_keys:
                    try:
                        related_issue = jira.issue(key)
                        jira.create_issue_link(type='Relates', inwardIssue=node.jira_issue.key, outwardIssue=related_issue.key)
                        logging.info(f"Linked {node.jira_issue.key} to {related_issue.key}")
                    except Exception as e:
                        logging.error(f"Error linking issue {node.jira_issue.key} to {key}: {e}")

            # Create or update child issues
            for child in node.children:
                # For Sub-tasks, set parent issue
                if child.type == 'Sub-task':
                    child.parent = node
                create_or_update_issue(child)

        for root in root_nodes:
            create_or_update_issue(root)

        # Save issue mapping to file
        try:
            with open(issue_mapping_file, 'w') as f:
                json.dump(issue_mapping, f, indent=4)
            logging.info(f"Issue mapping saved to '{issue_mapping_file}'")
        except Exception as e:
            logging.error(f"Error saving issue mapping to '{issue_mapping_file}': {e}")

        # Sleep for 5 minutes before the next run
        logging.info("Waiting for 5 minutes before the next run...")
        time.sleep(300)  # 300 seconds = 5 minutes

if __name__ == '__main__':
    main()
