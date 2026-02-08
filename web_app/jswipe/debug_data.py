from datetime import date

from web_app.jswipe.data_interface import JobPost


# Hardcoded jobs for debug mode
DEBUG_JOBS = [
    JobPost(
        id='debug-1',
        title='Software Engineer',
        company='TechCorp Australia',
        location='Sydney',
        description='We are looking for a skilled Software Engineer to join our team. You will work on exciting projects using Python, JavaScript, and cloud technologies. Experience with web frameworks like Flask or Django preferred.',
        url='https://example.com/job1',
        post_date=date.today()
    ),
    JobPost(
        id='debug-2',
        title='Senior Python Developer',
        company='DataFlow Systems',
        location='Melbourne',
        description='Join our data engineering team! We need a Senior Python Developer with experience in data processing, ETL pipelines, and machine learning. Remote work options available.',
        url='https://example.com/job2',
        post_date=date.today()
    ),
    JobPost(
        id='debug-3',
        title='Full Stack Developer',
        company='StartupXYZ',
        location='Brisbane',
        description='Fast-growing startup seeking a Full Stack Developer. Tech stack: React, Node.js, PostgreSQL. Must be comfortable with rapid iteration and agile development.',
        url='https://example.com/job3',
        post_date=date.today()
    ),
    JobPost(
        id='debug-4',
        title='DevOps Engineer',
        company='CloudNative Solutions',
        location='Perth',
        description='Looking for a DevOps Engineer with AWS, Kubernetes, and Terraform experience. You will help build and maintain our cloud infrastructure and CI/CD pipelines.',
        url='https://example.com/job4',
        post_date=date.today()
    ),
    JobPost(
        id='debug-5',
        title='Product Manager',
        company='FinTech Innovations',
        location='Sydney',
        description='Join our fintech team as a Product Manager. You will drive product strategy, work with engineering teams, and deliver features that help our customers. Finance background a plus.',
        url='https://example.com/job5',
        post_date=date.today()
    ),
]