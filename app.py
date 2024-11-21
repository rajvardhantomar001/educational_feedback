from flask import Flask, request, jsonify, session
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from langchain import OpenAI
import openai
import os
from PyPDF2 import PdfReader

# Flask app configuration
app = Flask(__name__)
app.secret_key = "supersecretkey"

# OpenAI Configuration
openai.api_base = "https://api.groq.com/openai/v1"
openai.api_key = os.environ.get("GROQ_API_KEY")

# MySQL database configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'school_app'
}

# Utility functions
def db_connection():
    return mysql.connector.connect(**db_config)

def extract_text_from_pdf(pdf_file):
    """Extract text from an uploaded PDF file."""
    pdf_reader = PdfReader(pdf_file)
    extracted_text = ""
    for page in pdf_reader.pages:
        extracted_text += page.extract_text()
    return extracted_text

# Routes
@app.route('/signup', methods=['POST'])
def signup():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')  # "student" or "teacher"

    if not all([username, password, role]):
        return jsonify({'error': 'All fields are required'}), 400

    if role not in ['student', 'teacher']:
        return jsonify({'error': 'Invalid role'}), 400

    hashed_password = generate_password_hash(password)

    try:
        conn = db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (username, hashed_password, role)
        )
        conn.commit()
        return jsonify({'message': 'User registered successfully!'})
    except mysql.connector.Error as err:
        return jsonify({'error': str(err)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    try:
        conn = db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            return jsonify({'message': 'Login successful', 'role': user['role']})
        return jsonify({'error': 'Invalid username or password'}), 401
    finally:
        cursor.close()
        conn.close()

@app.route('/create_test', methods=['POST'])
def create_test():
    if session.get('role') != 'teacher':
        return jsonify({'error': 'Access denied'}), 403

    data = request.json
    questions = data.get('questions')  # [{"question": ..., "type": "objective/subjective"}]

    if not questions:
        return jsonify({'error': 'Questions are required'}), 400

    try:
        conn = db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tests (teacher_id, questions) VALUES (%s, %s)",
            (session['user_id'], str(questions))
        )
        conn.commit()
        return jsonify({'message': 'Test created successfully!'})
    finally:
        cursor.close()
        conn.close()

@app.route('/submit_test', methods=['POST'])
def submit_test():
    if session.get('role') != 'student':
        return jsonify({'error': 'Access denied'}), 403

    data = request.json
    test_id = data.get('test_id')
    answers = data.get('answers')

    if not test_id or not answers:
        return jsonify({'error': 'Test ID and answers are required'}), 400

    try:
        conn = db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO submissions (student_id, test_id, answers) VALUES (%s, %s, %s)",
            (session['user_id'], test_id, str(answers))
        )
        conn.commit()
        return jsonify({'message': 'Test submitted successfully!'})
    finally:
        cursor.close()
        conn.close()

@app.route('/submit_project', methods=['POST'])
def submit_project():
    if session.get('role') != 'student':
        return jsonify({'error': 'Access denied'}), 403

    file = request.files['project_file']
    if file:
        filepath = f"uploads/{file.filename}"
        file.save(filepath)

        try:
            conn = db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO project_submissions (student_id, project_file) VALUES (%s, %s)",
                (session['user_id'], filepath)
            )
            conn.commit()
            return jsonify({'message': 'Project submitted successfully!'})
        finally:
            cursor.close()
            conn.close()
    return jsonify({'error': 'File upload failed'}), 400

@app.route('/evaluate_project', methods=['POST'])
def evaluate_project():
    if 'project_file' not in request.files:
        return jsonify({"error": "No project file uploaded"}), 400

    project_file = request.files['project_file']
    data = request.form

    # Extract text from the uploaded PDF
    project_text = extract_text_from_pdf(project_file)

    # Get other details from the form data
    question = data.get("question")
    max_marks = data.get("max_marks")
    class_stu = data.get("class_stu")
    subject = data.get("subject")
    chapter_or_topic = data.get("chapter_or_topic")
    project_title = data.get("project_title")

    # Generate prompt for OpenAI
    prompt = f'''
        You are a CBSE board project evaluator. You will be given the project details, maximum marks, expected deliverables, key parameters for evaluation, and marks distribution for each parameter. Based on the student's submission, evaluate the project by considering each parameter and deducting marks if requirements are not met.

        Question: {question}
        Class: {class_stu}
        Subject: {subject}
        Chapter/Topic: {chapter_or_topic}
        Project Title: {project_title}
        Maximum Marks: {max_marks}

        Below are the Evaluation Parameters: 
        1. Clarity of Objective
        2. Depth of Research/Content
        3. Originality and Creativity
        4. Presentation and Organization
        5. Practical Application or Relevance
        6. Adherence to Guidelines

        Judge the projects for on all the parameters. Divide maximum marks for all the parameters as per their importance. If any of the parameter is missing, deduct marks.

        The Student's Answer is: {str(project_text)}

        Keep the account of following points as well:
        1. Assess the project against each parameter listed above, assigning marks accordingly.
        2. Deduct marks if any parameter is partially or fully missing, or if it does not meet the expected standard.
        3. Provide specific feedback for areas needing improvement.

        Use CBSE board rules to evaluate the answer.
        Return the marks obtained and a suggestion to improve the answer.

        Return it in the form of marks obtained, the suggestion.
    '''

    # Call OpenAI API
    response = openai.ChatCompletion.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": "You are a helpful assistant who evaluates projects."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.5,
        max_tokens=1024,
        top_p=1,
        stop=None
    )

    evaluation = response['choices'][0]['message']['content']
    marks_obtained = evaluation.split(",")[0].split(":")[1].strip()  # Parse marks
    suggestion = evaluation.split(",")[1].split(":")[1].strip()  # Parse suggestion

    return jsonify({
        "marks_obtained": marks_obtained,
        "feedback": suggestion
    })

@app.route('/evaluate_test', methods=['POST'])
def evaluate_test():
    data = request.json
    test_id = data.get('test_id')
    student_answers = data.get('answers')  # JSON format: {"question_id": "answer"}

    # Fetch the test details
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT questions FROM tests WHERE id = %s", (test_id,))
    test = cursor.fetchone()
    if not test:
        return jsonify({"error": "Test not found"}), 404

    questions = json.loads(test['questions'])
    total_marks = 0
    feedback = []

    for question_id, question_data in questions.items():
        question_type = question_data['type']  # "objective" or "subjective"
        expected_answer = question_data['answer']
        max_marks = question_data['marks']
        class_stu = question_data.get('class', '')
        subject = question_data.get('subject', '')

        student_answer = student_answers.get(question_id, "")

        if question_type == "objective":
            # Evaluate objective question
            if student_answer.strip().lower() == expected_answer.strip().lower():
                total_marks += max_marks
                feedback.append({"question_id": question_id, "marks": max_marks, "feedback": "Correct answer"})
            else:
                feedback.append({"question_id": question_id, "marks": 0, "feedback": "Incorrect answer"})

        elif question_type == "subjective":
            # Evaluate subjective question using OpenAI
            prompt = f'''
                You are a CBSE board exam answer evaluator. You will be given with question, maximum marks, expected answer, points expected in the answer, marks for each point and the additional comments if any.
                Also you will be provided with class, subject, and chapter name from where the question is taken.

                Question: {question_data['question']}
                Maximum Marks: {max_marks}
                Expected Answer: {expected_answer}

                Divide the answers into points and assign each point marks. Accordingly evaluate the answer. If a point is missing deduct marks.

                Other details are:
                Class: {class_stu}
                Subject: {subject}

                The Student's Answer is: {str(student_answer)}

                Use CBSE board rules to evaluate the answer.
                Return the marks obtained and a suggestion to improve the answer.

                Return it in the form of marks obtained, the suggestion.
            '''

            response = openai.ChatCompletion.create(
                model="llama3-8b-8192",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant who generates quizzes."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=1024,
                top_p=1
            )

            evaluation = response['choices'][0]['message']['content']
            marks_obtained = int(evaluation.split(",")[0].split(":")[1].strip())  # Parse marks from response
            suggestion = evaluation.split(",")[1].split(":")[1].strip()  # Parse suggestion

            total_marks += marks_obtained
            feedback.append({
                "question_id": question_id,
                "marks": marks_obtained,
                "feedback": suggestion
            })

    return jsonify({"total_marks": total_marks, "feedback": feedback})

@app.route('/get_feedback', methods=['POST'])
def get_feedback():
    data = request.json

    # Extract evaluation parameters from the request
    test_scores = data.get("test_scores", "")
    essay_quality = data.get("essay_quality", "")
    conceptual_understanding = data.get("conceptual_understanding", "")
    participation_level = data.get("participation_level", "")
    time_on_tasks = data.get("time_on_tasks", "")
    engagement_level = data.get("engagement_level", "")
    assignment_grades = data.get("assignment_grades", "")
    quiz_grades = data.get("quiz_grades", "")
    project_grades = data.get("project_grades", "")
    word_count = data.get("word_count", "")
    sentence_length = data.get("sentence_length", "")
    structure_quality = data.get("structure_quality", "")
    flow_quality = data.get("flow_quality", "")
    key_term_usage = data.get("key_term_usage", "")
    explanation_quality = data.get("explanation_quality", "")
    grammar_accuracy = data.get("grammar_accuracy", "")
    punctuation_spelling_accuracy = data.get("punctuation_spelling_accuracy", "")
    time_on_writing = data.get("time_on_writing", "")
    revisions_count = data.get("revisions_count", "")

    # Generate the prompt
    prompt = f'''
        You are an academic evaluator providing detailed feedback to a student based on their performance across multiple cognitive, behavioral, and academic parameters, as well as their written content quality. The goal is to offer constructive and actionable feedback that helps the student improve and perform better.

        Below are the Evaluation Parameters: 
        Cognitive:
            Test Scores: {test_scores}
            Essay Quality: {essay_quality}
            Conceptual Understanding: {conceptual_understanding}
        Behavioral:
            Participation: {participation_level}
            Time Spent on Tasks: {time_on_tasks}
            Engagement Levels: {engagement_level}
        Academic:
            Grades in Assignments: {assignment_grades}
            Grades in Quizzes: {quiz_grades}
            Grades in Projects: {project_grades}
        Written Content:
            Text Length:
                Word Count: {word_count}
                Sentence Length: {sentence_length}
            Coherence:
                Structure: {structure_quality}
                Flow of Arguments: {flow_quality}
            Conceptual Depth:
                Use of Key Terms: {key_term_usage}
                Explanation Quality: {explanation_quality}
            Grammar:
                Grammatical Accuracy: {grammar_accuracy}
                Punctuation and Spelling: {punctuation_spelling_accuracy}
        Engagement:
            Time Spent on Writing: {time_on_writing}
            Revisions Made: {revisions_count}

        Keep the account of following points as well:
        1. Evaluate the student's performance in each category and sub-category based on the input provided.
        2. Provide specific feedback for each parameter:
               - Mention strengths and highlight areas that require improvement.
               - Offer actionable suggestions for better performance.
        3. Conclude with an overall summary that encourages the student and outlines their path for improvement.

        Output Format: 

        Cognitive Feedback:
            Test Scores:
            Essay Quality:
            Conceptual Understanding:
        Behavioral Feedback:

        Participation:
            Time Spent on Tasks:
            Engagement Levels:

        Academic Feedback:
            Grades in Assignments:
            Grades in Quizzes:
            Grades in Projects:

        Written Content Feedback:

            Text Length:
            Coherence:
            Conceptual Depth:
            Grammar:
            Engagement:

        Overall Summary:
            Strengths:
            Suggestions for Improvement:
            Motivational Note:
    '''

    response = openai.ChatCompletion.create(
        model="llama3-8b-8192",
        messages=[
            {"role": "system", "content": "You are a helpful assistant who evaluates students' performance."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.5,
        max_tokens=1024,
        top_p=1,
        stop=None
    )

    feedback = response['choices'][0]['message']['content']

    return jsonify({"feedback": feedback})

# Run the app
if __name__ == '__main__':
    app.run(debug=True)
