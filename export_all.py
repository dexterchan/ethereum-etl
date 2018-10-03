# MIT License
#
# Copyright (c) 2018 Evgeny Medvedev, evge.medvedev@gmail.com
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import argparse
import csv
import logging
import os

from time import time
from datetime import datetime
from web3 import Web3

from ethereumetl.jobs.export_blocks_job import ExportBlocksJob
from ethereumetl.jobs.export_token_transfers_job import ExportTokenTransfersJob
from ethereumetl.jobs.export_receipts_job import ExportReceiptsJob
from ethereumetl.jobs.export_contracts_job import ExportContractsJob
from ethereumetl.jobs.export_tokens_job import ExportTokensJob

from ethereumetl.jobs.exporters.blocks_and_transactions_item_exporter import blocks_and_transactions_item_exporter
from ethereumetl.jobs.exporters.token_transfers_item_exporter import token_transfers_item_exporter
from ethereumetl.jobs.exporters.receipts_and_logs_item_exporter import receipts_and_logs_item_exporter
from ethereumetl.jobs.exporters.contracts_item_exporter import contracts_item_exporter
from ethereumetl.jobs.exporters.tokens_item_exporter import tokens_item_exporter

from ethereumetl.logging_utils import logging_basic_config
from ethereumetl.providers.auto import get_provider_from_uri
from ethereumetl.thread_local_proxy import ThreadLocalProxy
from ethereumetl.csv_utils import set_max_field_size_limit
from ethereumetl.file_utils import smart_open

parser = argparse.ArgumentParser(description='Export all for a range of blocks.',
                                 usage='-s <start_block> -e <end_block> -b <batch_size> -p <provider_uri> [-o <output_dir> -w <max_workers>]')
parser.add_argument('-s', '--start-block', default=0, type=int, help='Start block')
parser.add_argument('-e', '--end-block', required=True, type=int, help='End block')
parser.add_argument('-b', '--batch-size', default=100, type=int, help='The number of blocks to export at a time.')
parser.add_argument('-p', '--provider-uri', default='https://mainnet.infura.io', type=str,
                    help='The URI of the web3 provider e.g. '
                         'file://$HOME/Library/Ethereum/geth.ipc or https://mainnet.infura.io')
parser.add_argument('-o', '--output-dir', default=None, type=str, help='Output directory, partitioned in Hive style.')
parser.add_argument('-w', '--max-workers', default=5, type=int, help='The maximum number of workers.')

args = parser.parse_args()

logger = logging.getLogger(__name__)
logging_basic_config()

def extract_csv_column_unique(input, output, column):
    set_max_field_size_limit()

    with smart_open(input, 'r') as input_file, smart_open(output, 'w') as output_file:
        reader = csv.DictReader(input_file)
        seen = set()  # set for fast O(1) amortized lookup
        for row in reader:
            if row[column] in seen:
                continue
            seen.add(row[column])
            output_file.write(row[column] + '\n')


for batch_start_block in range(args.start_block, args.end_block, args.batch_size):
    ### start
    start_time = time()
    batch_end_block = batch_start_block + args.batch_size - 1
    if batch_end_block > args.end_block:
        batch_end_block = args.end_block

    padded_batch_start_block = str(batch_start_block).zfill(8)
    padded_batch_end_block = str(batch_end_block).zfill(8)
    block_range = f'{padded_batch_start_block}-{padded_batch_end_block}'
    file_name_suffix = f'{padded_batch_start_block}_{padded_batch_end_block}'
    # Hive style partitioning
    partition_dir = f'/start_block={padded_batch_start_block}/end_block={padded_batch_end_block}'

    ### blocks_and_transactions
    blocks_output_dir = f'{args.output_dir}/blocks{partition_dir}'
    os.makedirs(os.path.dirname(blocks_output_dir), exist_ok=True)

    transactions_output_dir = f'{args.output_dir}/transactions{partition_dir}'
    os.makedirs(os.path.dirname(transactions_output_dir), exist_ok=True)

    blocks_file = f'{blocks_output_dir}/blocks_{file_name_suffix}.csv'
    transactions_file = f'{transactions_output_dir}/transactions_{file_name_suffix}.csv'
    logger.info(f'Exporting blocks {block_range} to {blocks_file}')
    logger.info(f'Exporting transactions from blocks {block_range} to {transactions_file}')

    job = ExportBlocksJob(
        start_block=batch_start_block,
        end_block=batch_end_block,
        batch_size=args.batch_size,
        batch_web3_provider=ThreadLocalProxy(lambda: get_provider_from_uri(args.provider_uri, batch=True)),
        max_workers=args.max_workers,
        item_exporter=blocks_and_transactions_item_exporter(blocks_file, transactions_file),
        export_blocks=blocks_file is not None,
        export_transactions=transactions_file is not None)
    job.run()

    ### token_transfers
    if 'infura' not in args.provider_uri:
        token_transfers_output_dir = f'{args.output_dir}/token_transfers{partition_dir}'
        os.makedirs(os.path.dirname(token_transfers_output_dir), exist_ok=True)

        token_transfers_file = f'{token_transfers_output_dir}/token_transfers_{file_name_suffix}.csv'
        logger.info(f'Exporting ERC20 transfers from blocks {block_range} to {token_transfers_file}')

        job = ExportTokenTransfersJob(
            start_block=batch_start_block,
            end_block=batch_end_block,
            batch_size=args.batch_size,
            web3=ThreadLocalProxy(lambda: Web3(get_provider_from_uri(args.provider_uri))),
            item_exporter=token_transfers_item_exporter(token_transfers_file),
            max_workers=args.max_workers)
        job.run()

    ### receipts_and_logs
    transaction_hashes_output_dir = f'{args.output_dir}/transaction_hashes{partition_dir}'
    os.makedirs(os.path.dirname(transaction_hashes_output_dir), exist_ok=True)

    transaction_hashes_file = f'{transaction_hashes_output_dir}/transaction_hashes_{file_name_suffix}.csv'
    logger.info(f'Extracting hash column from transaction file {transactions_file}')
    extract_csv_column_unique(transactions_file, transaction_hashes_file, 'hash')

    receipts_output_dir = f'{args.output_dir}/receipts{partition_dir}'
    os.makedirs(os.path.dirname(receipts_output_dir), exist_ok=True)

    logs_output_dir = f'{args.output_dir}/logs{partition_dir}'
    os.makedirs(os.path.dirname(logs_output_dir), exist_ok=True)

    receipts_file = f'{receipts_output_dir}/receipts_{file_name_suffix}.csv'
    logs_file = f'{logs_output_dir}/logs_{file_name_suffix}.csv'
    logger.info(f'Exporting receipts and logs from blocks {block_range} to {receipts_file} and {logs_file}')

    with smart_open(transaction_hashes_file, 'r') as transaction_hashes:
        job = ExportReceiptsJob(
            transaction_hashes_iterable=(transaction_hash.strip() for transaction_hash in transaction_hashes),
            batch_size=args.batch_size,
            batch_web3_provider=ThreadLocalProxy(lambda: get_provider_from_uri(args.provider_uri, batch=True)),
            max_workers=args.max_workers,
            item_exporter=receipts_and_logs_item_exporter(receipts_file, logs_file),
            export_receipts=receipts_file is not None,
            export_logs=logs_file is not None)
        job.run()

    ### contracts
    contract_addresses_output_dir = f'{args.output_dir}/contract_addresses{partition_dir}'
    os.makedirs(os.path.dirname(contract_addresses_output_dir), exist_ok=True)

    contract_addresses_file = f'{contract_addresses_output_dir}/contract_addresses_{file_name_suffix}.csv'
    logger.info(f'Extracting contract_address from receipt file {receipts_file}')
    extract_csv_column_unique(receipts_file, contract_addresses_file, 'contract_address')

    contracts_output_dir = f'{args.output_dir}/contracts{partition_dir}'
    os.makedirs(os.path.dirname(contracts_output_dir), exist_ok=True)

    contracts_file = f'{contracts_output_dir}/contracts_{file_name_suffix}.csv'
    logger.info(f'Exporting contracts from blocks {block_range} to {contracts_file}')

    with smart_open(contract_addresses_file, 'r') as contract_addresses_file:
        contract_addresses = (contract_address.strip() for contract_address in contract_addresses_file
                              if contract_address.strip())
        job = ExportContractsJob(
            contract_addresses_iterable=contract_addresses,
            batch_size=args.batch_size,
            batch_web3_provider=ThreadLocalProxy(lambda: get_provider_from_uri(args.provider_uri, batch=True)),
            item_exporter=contracts_item_exporter(contracts_file),
            max_workers=args.max_workers)
        job.run()

    ### tokens
    if 'infura' not in args.provider_uri:
        token_addresses_output_dir = f'{args.output_dir}/token_addresses{partition_dir}'
        os.makedirs(os.path.dirname(token_addresses_output_dir), exist_ok=True)

        token_addresses_file = f'{token_addresses_output_dir}/token_addresses_{file_name_suffix}'
        logger.info(f'Extracting token_address from token_transfers file {token_transfers_file}')
        extract_csv_column_unique(token_transfers_file, token_addresses_file, 'token_address')

        tokens_output_dir = f'{args.output_dir}/tokens{partition_dir}'
        os.makedirs(os.path.dirname(tokens_output_dir), exist_ok=True)

        tokens_file = f'{tokens_output_dir}/tokens_{file_name_suffix}.csv'
        logger.info(f'Exporting tokens from blocks {block_range} to {tokens_file}')

        with smart_open(token_addresses_file, 'r') as token_addresses:
            job = ExportTokensJob(
                token_addresses_iterable=(token_address.strip() for token_address in token_addresses),
                web3=ThreadLocalProxy(lambda: Web3(get_provider_from_uri(args.provider_uri))),
                item_exporter=tokens_item_exporter(token_addresses_file),
                max_workers=args.max_workers)
            job.run()

    ### finish
    end_time = time()
    time_diff = round(end_time - start_time, 5)
    logger.info(f'Exporting blocks {block_range} took {time_diff} seconds')
