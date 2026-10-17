output "request_queue_url" { value = aws_sqs_queue.requests.url }
output "dead_letter_queue_url" { value = aws_sqs_queue.dead_letters.url }
output "report_bucket" { value = aws_s3_bucket.reports.bucket }
output "results_topic_arn" { value = aws_sns_topic.results.arn }
output "scanner_instance_id" { value = aws_instance.scanner.id }
