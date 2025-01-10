from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import os
import boto3
import dotenv
from datetime import datetime
from pathlib import Path
from io import BytesIO
import pytz
TIMEZONE = pytz.timezone('Asia/Kolkata')

# Load environment variables
dotenv.load_dotenv()

app = Flask(__name__)

# Configure AWS
aws_region = os.getenv('AWS_REGION', 'us-east-1')

# Initialize AWS services
s3 = boto3.client('s3', region_name=aws_region)
rekognition = boto3.client('rekognition', region_name=aws_region)
dynamodb = boto3.resource('dynamodb', region_name=aws_region).Table('Tableaws')

# Constants
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'shailendrasirclasses')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB

# Configure CORS

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', 'https://master.dl76w5z8gkkdv.amplifyapp.com')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    
    # Handle preflight requests
    if request.method == 'OPTIONS':
        response.headers.add('Access-Control-Max-Age', '1728000')
        response.headers['Content-Type'] = 'text/plain'
        response.headers['Content-Length'] = '0'
        return response

    return response
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ensure_s3_folder_exists(bucket, folder_path):
    try:
        s3.list_objects_v2(Bucket=bucket, Prefix=folder_path)
        s3.put_object(
            Bucket=bucket,
            Key=f"{folder_path}dummy.txt",
            Body='This is a placeholder file to ensure the folder exists.'
        )
    except Exception as e:
        print(f"Error ensuring S3 folder exists: {e}")
        raise

import re
def get_formatted_time():
    """Get current time in Indian timezone with proper formatting"""
    return datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')

def get_current_date():
    """Get current date in Indian timezone"""
    return datetime.now(TIMEZONE).strftime('%Y-%m-%d')

@app.route('/classes', methods=['GET', 'OPTIONS'])
def get_classes():
    # Handle preflight request
    if request.method == 'OPTIONS':
        return '', 200

    try:
        # Your existing code for GET request
        response = s3.list_objects_v2(
            Bucket=S3_BUCKET_NAME,
            Prefix='classes/',
            Delimiter='/'
        )
        
        classes = []
        pattern = r'classes/([^/]+)/'
        if 'CommonPrefixes' in response:
            for item in response['CommonPrefixes']:
                prefix = item.get('Prefix', '')
                match = re.search(pattern, prefix)
                if match:
                    classes.append(match.group(1))
        
        return jsonify({
            'success': True,
            'classes': classes
        })
    except Exception as e:
        print(f"Error fetching classes: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400



def upload_to_s3(file_data, class_name, student_name):
    try:
        filename = "image.jpg"  # Generic filename since we're using BytesIO
        key = f"classes/{class_name}/{student_name}/{datetime.now().timestamp()}_{filename}"
        s3.upload_fileobj(
            file_data,
            S3_BUCKET_NAME,
            key,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
        return key
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        raise


def record_attendance_in_dynamodb(class_name, student_name, status):
    try:
        current_date = get_current_date()
        formatted_time = get_formatted_time()

        dynamodb.put_item(Item={
            'awstable': f"{class_name}-{student_name}",
            'className': class_name,
            'studentName': student_name,
            'date': current_date,
            'timestamp': formatted_time,
            'status': status,
            'lastAttendanceDate': current_date
        })
    except Exception as e:
        print(f"Error recording attendance in DynamoDB: {e}")
        raise
def get_today_attendance_record(class_name, student_name):
    try:
        today = get_current_date()
        response = dynamodb.get_item(
            Key={'awstable': f"{class_name}-{student_name}"}
        )
        if 'Item' in response:
            item = response['Item']
            return item.get('date') == today, item.get('status')
        return False, None
    except Exception as e:
        print(f"Error getting attendance record: {e}")
        raise
def update_attendance_in_dynamodb(class_name, student_name, status):
    try:
        formatted_time = get_formatted_time()
        dynamodb.update_item(
            Key={'awstable': f"{class_name}-{student_name}"},
            UpdateExpression="set #status = :status, #timestamp = :timestamp",
            ExpressionAttributeNames={
                "#status": "status",
                "#timestamp": "timestamp"
            },
            ExpressionAttributeValues={
                ":status": status,
                ":timestamp": formatted_time
            }
        )
    except Exception as e:
        print(f"Error updating attendance in DynamoDB: {e}")
        raise
def compare_faces(source_image, target_image_key):
    try:
        response = rekognition.compare_faces(
            SourceImage={'Bytes': source_image.read()},
            TargetImage={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': target_image_key}},
            SimilarityThreshold=90
        )
        return len(response.get('FaceMatches', [])) > 0
    except Exception as e:
        print(f"Error comparing faces: {e}")
        raise

@app.route('/hi', methods=['GET'])
def hello():
    return 'Hello World'

@app.route('/attendance/download', methods=['GET'])
def download_attendance():
    class_name = request.args.get('class')
    if not class_name:
        return jsonify({'success': False, 'message': 'Class name is required'}), 400

    try:
        # Fetch data from DynamoDB for the given class
        response = dynamodb.scan(
            FilterExpression="className = :className",
            ExpressionAttributeValues={":className": class_name}
        )
        records = response.get('Items', [])

        # Convert records to CSV format
        csv_data = "Student Name,Date,Status\n"
        for record in records:
            csv_data += f"{record['studentName']},{record['date']},{record['status']},{record['timestamp']}\n"

        # Return CSV as downloadable content
        return csv_data, 200, {
            'Content-Disposition': f'attachment; filename={class_name}_attendance.csv',
            'Content-Type': 'text/csv'
        }

    except Exception as e:
        print(f"Error fetching attendance records: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({'message': 'Welcome to the Flask App! This is the default page.'})


@app.route('/upload', methods=['POST'])
def upload():
    try:
        # Validate file
        if 'image' not in request.files:
            return jsonify({'success': False, 'message': 'No file uploaded'}), 400

        file = request.files['image']
        if not file or not allowed_file(file.filename):
            return jsonify({'success': False, 'message': 'Invalid file type'}), 400

        # Get form data
        class_name = request.form.get('class')
        student_name = request.form.get('name', '')  # Default to empty string
        folder = request.form.get('folder')

        # Validate required fields
        if not class_name:
            return jsonify({'success': False, 'message': 'Class name is required'}), 400
        if not folder:
            return jsonify({'success': False, 'message': 'Folder type is required'}), 400
        if folder == 'existing' and not student_name:
            return jsonify({'success': False, 'message': 'Student name is required for registration'}), 400

        # Read file once and store in memory
        file_data = BytesIO(file.read())

        if folder == 'existing':
            # Reset file pointer and upload
            file_data.seek(0)
            s3_key = upload_to_s3(file_data, class_name, student_name)
            record_attendance_in_dynamodb(class_name, student_name, 'Registered')
            return jsonify({
                'success': True,
                'message': f'Face registered for {student_name}'
            })
        else:
            response = s3.list_objects_v2(
                Bucket=S3_BUCKET_NAME,
                Prefix=f'classes/{class_name}/'
            )

            recognized_name = None

            for item in response.get('Contents', []):
                if any(item['Key'].endswith(ext) for ext in ALLOWED_EXTENSIONS):
                    # Reset file pointer for each comparison
                    file_data.seek(0)
                    if compare_faces(file_data, item['Key']):
                        recognized_name = item['Key'].split('/')[2]
                        break

            if not recognized_name:
                return jsonify({
                    'success': False,
                    'message': 'Face recognition is unmatched among the students'
                }), 400

            # Check if already marked attendance/checked out today
            has_record_today, current_status = get_today_attendance_record(class_name, recognized_name)
            status = 'Present' if folder == 'attendance' else 'Checked Out'

            if has_record_today:
                if folder == 'attendance' and current_status in ['Present', 'Checked Out']:
                    return jsonify({
                        'success': False,
                        'message': f'{recognized_name} has already marked attendance today'
                    }), 400
                elif folder == 'checkout':
                    if current_status == 'Checked Out':
                        return jsonify({
                            'success': False,
                            'message': f'{recognized_name} has already checked out today'
                        }), 400
                    elif current_status != 'Present':
                        return jsonify({
                            'success': False,
                            'message': f'{recognized_name} must mark attendance before checking out'
                        }), 400

            update_attendance_in_dynamodb(class_name, recognized_name, status)

            return jsonify({
                'success': True,
                'message': f'{status} marked for {recognized_name}',
                'matchDetails': {
                    'recognizedName': recognized_name,
                    'status': status,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            })

    except Exception as e:
        print(f"Error processing upload: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
