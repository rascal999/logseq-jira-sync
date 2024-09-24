#!/usr/bin/env python

import sys
import os
import json
import re  # Regular expressions for link conversion
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
        self.description_lines = []  # Now stores tuples of (indent, line)
        self.relations = []
        self.jira_issue = None
        self.path = ''

def convert_markdown_links_to_jira(text):
    # Regular expression pattern to find markdown links: [text](URL)
    pattern = r'\[([^\]]+)\]\(([^\)]+)\)'
    replacement = r'[\1|\2]'
    return re.sub(pattern, replacement, text)

def main():
    # Load environment variables from .env file
    load_dotenv()

    # Get Jira credentials and project key from environment variables
    jira_server = os.environ.get('JIRA_SERVER')
    jira_user = os.environ.get('JIRA_USER')
    jira_token = os.environ.get('JIRA_TOKEN')
    project_key = os.environ.get('JIRA_PROJECT_KEY')

    if not all([jira_server, jira_user, jira_token, project_key]):
        print("Error: Jira credentials and project key must be set in the .env file.")
        sys.exit(1)

    options = {'server': jira_server}
    jira = JIRA(options, basic_auth=(jira_user, jira_token))

    filename = 'input.txt'  # Or get from sys.argv
    if len(sys.argv) > 1:
        filename = sys.argv[1]

    # Load issue mapping from file
    mapping_filename = 'issue_mapping.json'
    if os.path.exists(mapping_filename):
        with open(mapping_filename, 'r') as f:
            issue_mapping = json.load(f)
    else:
        issue_mapping = {}

    with open(filename, 'r') as f:
        lines = f.readlines()

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
                    print("Error: Tickets beyond sub-task detected. Exiting.")
                    sys.exit(1)
            elif node_content.startswith('#'):
                # It's a relation
                if current_node:
                    current_node.relations.append(node_content)
                else:
                    # No current node to attach to
                    print(f"Warning: Relation '{node_content}' found with no parent node.")
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
                    # No current node to attach to
                    print(f"Warning: Relation '{content}' found with no parent node.")
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
                print(f"Updating {node.type} '{node.line}' with key {issue.key}")
                # Process description
                description_text = build_description_text(node.description_lines)
                description_text = convert_markdown_links_to_jira(description_text)
                # Update summary and description
                issue.update(summary=node.line, description=description_text)
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
                        jira.transition_issue(issue, transition_id)
                        print(f"Set status of {issue.key} to {jira_status}")
            except Exception as e:
                print(f"Error updating issue {issue_key}: {e}")
                # Remove from mapping and recreate
                del issue_mapping[node.path]
                create_or_update_issue(node)
                return
        else:
            # Issue does not exist, create it
            # Process description
            description_text = build_description_text(node.description_lines)
            description_text = convert_markdown_links_to_jira(description_text)

            issue_dict = {
                'project': {'key': project_key},
                'summary': node.line,
                'description': description_text,
                'issuetype': {'name': node.type},
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
            issue = jira.create_issue(fields=issue_dict)
            node.jira_issue = issue
            print(f"Created {node.type} '{node.line}' with key {issue.key}")
            # Add to mapping
            issue_mapping[node.path] = issue.key

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
                    jira.transition_issue(issue, transition_id)
                    print(f"Set status of {issue.key} to {jira_status}")

        # Handle relations
        for relation in node.relations:
            # Implement relation handling
            print(f"Handling relation '{relation}' for issue {node.jira_issue.key}")
            # Extract issue keys from relation
            related_issue_keys = re.findall(r'#([A-Z]+-\d+)', relation)
            for key in related_issue_keys:
                try:
                    related_issue = jira.issue(key)
                    jira.create_issue_link(type='Relates', inwardIssue=node.jira_issue.key, outwardIssue=related_issue.key)
                    print(f"Linked {node.jira_issue.key} to {related_issue.key}")
                except Exception as e:
                    print(f"Error linking issue {node.jira_issue.key} to {key}: {e}")

        # Create or update child issues
        for child in node.children:
            # For Sub-tasks, set parent issue
            if child.type == 'Sub-task':
                child.parent = node
            create_or_update_issue(child)

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

    for root in root_nodes:
        create_or_update_issue(root)

    # Save issue mapping to file
    with open(mapping_filename, 'w') as f:
        json.dump(issue_mapping, f, indent=4)

if __name__ == '__main__':
    main()
