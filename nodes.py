
from mcp_connection import mcp_manager
from langchain_core import prompt_values
from pydantic import BaseModel, Field
from typing import Literal
from langchain_openrouter import ChatOpenRouter
from tavily import TavilyClient
import logging
import os
import json
from dotenv import load_dotenv
load_dotenv()
from tools import get_jobs
from pydantic_models import FetchedJobDetails
from prompts import load_prompt
# from mcp_connection.mcp_client import get_tools
# import asyncio

# chrome_tools = asyncio.run(get_tools())

class AgentOutput(BaseModel):
    next_action: Literal["CHAT", "FETCH_JOB_DESCRIPTION", "VERIFY_JOB"]
    message: str = Field(description="The message to be shown to the user.")
    custom_job_url: str = Field(default="",description = "The custom job URL provided by user")
    num_jobs_to_process: int = Field(default=0,description = "Number of jobs to process from the excel file")
    excel_jobs_list: list[dict] = Field(default_factory=list, description = "List of excel jobs to be processed")


model = ChatOpenRouter(
    model="deepseek/deepseek-v4-flash",
    api_key = os.getenv("OPENROUTER_API_KEY")
)

tools_by_name = {
    "get_jobs": get_jobs
}


model_with_tools = model.bind_tools(tools = [get_jobs])
structured_model = model_with_tools.with_structured_output(AgentOutput)
logger = logging.getLogger(__name__)


def print_node_stage(node_name: str, stage: str) -> None:
    """Record node progress without cluttering normal CLI output."""
    logger.debug("Node %s: %s", node_name, stage)


def _redact_sensitive_values(value):
    """Keep tool-call logging useful without printing credentials to the console."""
    sensitive_keys = {"password", "token", "secret", "authorization", "api_key"}

    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in sensitive_keys else _redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def print_tool_calls(node_name: str, tool_calls) -> None:
    """Record requested tools at debug level, with sensitive values redacted."""
    logger.debug("Tool calls | Node: %s | Count: %s", node_name, len(tool_calls))

    for index, tool_call in enumerate(tool_calls, start=1):
        tool_name = tool_call["name"]
        tool_args = _redact_sensitive_values(tool_call["args"])
        formatted_args = json.dumps(tool_args, indent=2, default=str)

        logger.debug("Tool %s (%s): %s", index, tool_name, formatted_args)


def print_tool_result(node_name: str, tool_name: str) -> None:
    logger.debug("%s: %s completed", node_name, tool_name)

def chat_with_user(state):
    node_name = "chat_with_user"
    print_node_stage(node_name, "Started — waiting for user input")
    user_profile = state["user_profile"]
    resume = state["resume_data"]

    messages = state["messages"]

    user_input = input("You : > ")

    messages.append({"role": "user", "content": user_input})

    while True:
        response = model_with_tools.invoke(messages)
        messages.append(response)

        if response.tool_calls:
            print_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = tools_by_name[tool_name].invoke(tool_args)
                print_tool_result(node_name, tool_name)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
                break
        else:
            print_node_stage(node_name, "Agent response complete — producing routing decision")
            structured_response = structured_model.invoke(messages)
            print(structured_response.message)
            logger.debug("Next action: %s", structured_response.next_action)

            messages.append(structured_response.message)
            break

    return {"messages": messages,
            "next_action": structured_response.next_action,
            "job_url": structured_response.custom_job_url,
            "num_jobs_to_process": structured_response.num_jobs_to_process,
            "excel_jobs_list": structured_response.excel_jobs_list
            }



from playwright.async_api import async_playwright


async def fetch_job_description(state):
    node_name = "fetch_job_description"
    print_node_stage(node_name, "Started — loading the job page")
    job_url = state['job_url']

    logger.debug("Job URL: %s", job_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page()

        await page.goto(
            job_url,
            wait_until="domcontentloaded",
            timeout=30_000
        )

        await page.wait_for_timeout(2000)

        page_text = await page.locator("body").inner_text()

        structured_job_model = model.with_structured_output(FetchedJobDetails)

        messages = [
            {"role": "system", "content": load_prompt("fetch_job_description/system.txt")},
            {"role": "user", "content": load_prompt(
                "fetch_job_description/user.txt",
                job_url=job_url,
                page_text=page_text,
            )}
        ]

        job_details = structured_job_model.invoke(messages)

        print_node_stage(node_name, "Completed — job details extracted")
        logger.debug("Extracted job details: %s", job_details)
        await browser.close()

    return {
        "job_details": job_details
    }

import pandas as pd

def insert_into_excel_file(state):
    node_name = "insert_into_excel_file"
    print_node_stage(node_name, "Started — preparing the spreadsheet update")

    job_details = state["job_details"]
    excel_file_path = state["excel_file_path"]

    df = pd.read_excel(excel_file_path)

    # Pydantic model -> dictionary
    job_dict = job_details.model_dump()

    # dictionary -> one-row DataFrame
    new_row = pd.DataFrame([job_dict])

    df = pd.concat([df, new_row], ignore_index=True)

    df.to_excel(excel_file_path, index=False)

    print_node_stage(node_name, "Completed — job added to the spreadsheet")

    return {}



from mcp_connection.mcp_manager import MCPManager


# chrome_model_with_tools = model.bind_tools(tools = chrome_tools)

class JobVerifierOutput(BaseModel):
    is_valid: bool = Field(description = "True if the job is valid, False otherwise")
    message: str = Field(description = "The message to be shown to the user.")

structured_job_verifier_model = model.with_structured_output(JobVerifierOutput)

async def job_verifier_node(state):
    node_name = "job_verifier_node"
    print_node_stage(node_name, "Started — connecting to browser tools")
    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()
    logger.debug("Browser tools available: %s", ', '.join(tool.name for tool in tools))
    model_with_tools = model.bind_tools(tools = tools)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    job_url = state['job_url']
    logger.debug("Job URL: %s", job_url)

    messages = [
        {'role': 'system', 'content': load_prompt("job_verifier/system.txt")},
        {'role':'user', 'content': load_prompt("job_verifier/user.txt", job_url=job_url)}
    ]

    while True:

        response = await model_with_tools.ainvoke(messages)
        messages.append(response)
        if response.tool_calls:
            print_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await tools_by_name[tool_name].ainvoke(tool_args)
                print_tool_result(node_name, tool_name)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            print_node_stage(node_name, "Completed — evaluating job eligibility")
            structured_response = structured_job_verifier_model.invoke(messages)
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()
    return {
        "is_valid": structured_response.is_valid
    }


class LoginRequirementOutput(BaseModel):
    requires_login : bool
    actual_job_url: str

async def navigate_to_application(state):
    node_name = "navigate_to_application"
    print_node_stage(node_name, "Started — locating the application page")
    job_url = state['job_url']
    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()
    logger.debug("Browser tools available: %s", ', '.join(tool.name for tool in tools))
    model_with_tools = model.bind_tools(tools = tools)
    structured_model = model.with_structured_output(LoginRequirementOutput)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    messages = [
        {'role': 'system', 'content': load_prompt("navigate_to_application/system.txt")},
        {'role':'user', 'content': load_prompt("navigate_to_application/user.txt", job_url=job_url)}
    ]

    while True:

        response = await model_with_tools.ainvoke(messages)
        messages.append(response)
        if response.tool_calls:
            print_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await tools_by_name[tool_name].ainvoke(tool_args)
                print_tool_result(node_name, tool_name)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            print_node_stage(node_name, "Completed — checking login requirement")
            structured_response = structured_model.invoke(messages)
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()
    return {
        "requires_login": structured_response.requires_login,
        "actual_job_url": structured_response.actual_job_url,
    }


class LoginOutput(BaseModel):
    is_logged_in : bool = Field(description = "True if the login is successful, False otherwise")
    logged_in_url: str = Field(description = "The url to navigate to after login")

structured_login_output = model.with_structured_output(LoginOutput)


async def perform_login(state):
    node_name = "perform_login"
    print_node_stage(node_name, "Started — signing in to the application site")

    job_url = state['actual_job_url']

    mcp_manager = MCPManager(["chrome", "gmail"])
    tools = await mcp_manager.start()
    logger.debug("Browser/email tools available: %s", ', '.join(tool.name for tool in tools))
    model_with_tools = model.bind_tools(tools = tools)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    messages = [
        {'role': 'system', 'content': load_prompt("perform_login/system.txt")},
        {'role':'user', 'content': load_prompt("perform_login/user.txt", job_url=job_url)}
    ]

    while True:

        response = await model_with_tools.ainvoke(messages)
        messages.append(response)
        if response.tool_calls:
            print_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await tools_by_name[tool_name].ainvoke(tool_args)
                print_tool_result(node_name, tool_name)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            print_node_stage(node_name, "Completed — recording login result")
            structured_response = structured_login_output.invoke(messages)
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()



async def apply_for_job(state):
    node_name = "apply_for_job"
    print_node_stage(node_name, "Started — completing the job application")
    job_url = state["actual_job_url"]

    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()
    logger.debug("Browser tools available: %s", ', '.join(tool.name for tool in tools))
    model_with_tools = model.bind_tools(tools = tools)
    structured_model = model.with_structured_output(LoginRequirementOutput)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    messages = [
        {'role': 'system', 'content': load_prompt("apply_for_job/system.txt")},
        {'role':'user', 'content': load_prompt(
            "apply_for_job/user.txt",
            job_url=job_url,
            user_profile=state['user_profile'],
            resume_data=state['resume_data'],
            resume_file_path=state['resume_file_path'],
        )}
    ]

    while True:

        response = await model_with_tools.ainvoke(messages)
        messages.append(response)
        if response.tool_calls:
            print_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await tools_by_name[tool_name].ainvoke(tool_args)
                print_tool_result(node_name, tool_name)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            print_node_stage(node_name, "Completed — recording application status")
            structured_response = structured_model.invoke(messages)
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()
    return {
        "requires_login": structured_response.requires_login,
        "actual_job_url": structured_response.actual_job_url,
    }
