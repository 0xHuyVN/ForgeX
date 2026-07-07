"""
Job Step Tracker - Track detailed job progress with timeline visualization

This module provides fine-grained tracking of job execution steps,
enabling timeline visualization and better debugging.
"""
from datetime import datetime
from ..database import db_cursor


class JobTracker:
    """Track job steps for timeline visualization"""
    
    def __init__(self, queue_item_id: int):
        self.queue_item_id = queue_item_id
        self.current_step = None
        
        # FIX BUG 1: Get actual job start time from database, not now()
        with db_cursor() as cur:
            # Try to get earliest step start time
            row = cur.execute(
                """
                SELECT MIN(started_at) as job_start 
                FROM job_steps 
                WHERE queue_item_id = ?
                """,
                (queue_item_id,)
            ).fetchone()
            
            if row and row['job_start']:
                # Use earliest step time
                self.start_time = datetime.strptime(row['job_start'], "%Y-%m-%d %H:%M:%S")
            else:
                # No steps yet, check queue_item created_at
                queue_row = cur.execute(
                    "SELECT created_at FROM queue_items WHERE id = ?",
                    (queue_item_id,)
                ).fetchone()
                
                if queue_row and queue_row['created_at']:
                    self.start_time = datetime.strptime(queue_row['created_at'], "%Y-%m-%d %H:%M:%S")
                else:
                    # Fallback to now
                    self.start_time = datetime.now()
    
    def start_step(self, step_name: str, step_index: int = 0, metadata: dict = None):
        """Start a new step"""
        import json
        
        # Complete previous step if exists
        if self.current_step:
            self.complete_step(self.current_step)
        
        self.current_step = step_name
        
        # FIX BUG 2: Check if step already exists (prevent duplicates)
        with db_cursor() as cur:
            existing = cur.execute(
                """
                SELECT id FROM job_steps 
                WHERE queue_item_id = ? AND step_index = ?
                """,
                (self.queue_item_id, step_index)
            ).fetchone()
            
            if existing:
                # Update existing instead of creating duplicate
                cur.execute(
                    """
                    UPDATE job_steps 
                    SET step_name = ?, status = 'running', started_at = ?, 
                        progress = 0, error = NULL, metadata = ?
                    WHERE id = ?
                    """,
                    (
                        step_name,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        json.dumps(metadata or {}),
                        existing['id']
                    )
                )
                step_id = existing['id']
            else:
                # Create new step
                cur.execute(
                    """
                    INSERT INTO job_steps 
                    (queue_item_id, step_name, step_index, status, started_at, metadata)
                    VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (
                        self.queue_item_id,
                        step_name,
                        step_index,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        json.dumps(metadata or {})
                    )
                )
                step_id = cur.lastrowid
        
        # Add log entry
        self._log('info', f'Started: {step_name}')
        
        # FIX BUG 3: Emit real-time event
        self._emit_timeline_update()
        
        return step_id
    
    def update_step_progress(self, step_name: str, progress: float):
        """Update progress of current step"""
        with db_cursor() as cur:
            cur.execute(
                """
                UPDATE job_steps 
                SET progress = ?
                WHERE queue_item_id = ? AND step_name = ? AND status = 'running'
                """,
                (progress, self.queue_item_id, step_name)
            )
        
        # FIX BUG 3: Emit real-time event on progress update
        self._emit_timeline_update()
    
    def complete_step(self, step_name: str, error: str = None):
        """Mark step as completed or failed"""
        status = 'failed' if error else 'completed'
        
        with db_cursor() as cur:
            # Get start time to calculate duration
            row = cur.execute(
                """
                SELECT started_at FROM job_steps
                WHERE queue_item_id = ? AND step_name = ? AND status = 'running'
                ORDER BY id DESC LIMIT 1
                """,
                (self.queue_item_id, step_name)
            ).fetchone()
            
            if row:
                started_at = datetime.strptime(row['started_at'], "%Y-%m-%d %H:%M:%S")
                duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
                
                cur.execute(
                    """
                    UPDATE job_steps 
                    SET status = ?, completed_at = ?, duration_ms = ?, progress = 100, error = ?
                    WHERE queue_item_id = ? AND step_name = ? AND status = 'running'
                    """,
                    (
                        status,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        duration_ms,
                        error,
                        self.queue_item_id,
                        step_name
                    )
                )
        
        if error:
            self._log('error', f'Failed: {step_name} - {error}')
        else:
            self._log('info', f'Completed: {step_name}')
        
        if self.current_step == step_name:
            self.current_step = None
        
        # FIX BUG 3: Emit real-time event
        self._emit_timeline_update()
    
    def fail_step(self, step_name: str, error: str):
        """Mark step as failed"""
        self.complete_step(step_name, error=error)
    
    def get_steps(self):
        """Get all steps for this job"""
        with db_cursor() as cur:
            rows = cur.execute(
                """
                SELECT * FROM job_steps
                WHERE queue_item_id = ?
                ORDER BY step_index, started_at
                """,
                (self.queue_item_id,)
            ).fetchall()
            return [dict(r) for r in rows]
    
    def get_timeline(self):
        """Get formatted timeline for UI display"""
        steps = self.get_steps()
        timeline = []
        
        # FIX BUG 5: Calculate total duration and ETA
        total_duration_ms = 0
        completed_steps = []
        running_step = None
        
        for step in steps:
            started = step.get('started_at')
            completed = step.get('completed_at')
            status = step.get('status', 'pending')
            
            # Calculate elapsed time from job start
            if started:
                try:
                    started_dt = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
                    elapsed_seconds = (started_dt - self.start_time).total_seconds()
                    elapsed_str = self._format_duration(elapsed_seconds)
                except:
                    elapsed_str = '00:00'
            else:
                elapsed_str = '00:00'
            
            # Status icon
            if status == 'completed':
                icon = '✓'
                if step.get('duration_ms'):
                    total_duration_ms += step['duration_ms']
                completed_steps.append(step)
            elif status == 'running':
                icon = '⟳'
                running_step = step
            elif status == 'failed':
                icon = '✗'
            else:
                icon = ' '
            
            # FIX BUG 5: Calculate ETA for this step
            eta_str = None
            if status == 'running' and step.get('progress', 0) > 0:
                # Estimate remaining time based on progress
                if started:
                    try:
                        started_dt = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
                        elapsed = (datetime.now() - started_dt).total_seconds()
                        progress = step.get('progress', 0) / 100
                        if progress > 0:
                            total_estimated = elapsed / progress
                            remaining = total_estimated - elapsed
                            if remaining > 0:
                                eta_str = self._format_duration(remaining) + ' remaining'
                    except:
                        pass
            
            timeline.append({
                'time': elapsed_str,
                'icon': icon,
                'name': step.get('step_name', ''),
                'status': status,
                'progress': step.get('progress', 0),
                'duration_ms': step.get('duration_ms'),
                'error': step.get('error'),
                'eta': eta_str,
            })
        
        return timeline
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration as MM:SS"""
        if seconds < 0:
            return '00:00'
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f'{minutes:02d}:{secs:02d}'
    
    def _log(self, level: str, message: str):
        """Add log entry"""
        try:
            with db_cursor() as cur:
                cur.execute(
                    "INSERT INTO job_logs (queue_item_id, level, message) VALUES (?,?,?)",
                    (self.queue_item_id, level, f'[tracker] {message}')
                )
        except:
            pass
    
    def _emit_timeline_update(self):
        """FIX BUG 3: Emit real-time event when timeline changes"""
        try:
            from .event_bus import event_bus
            event_bus.publish('timeline_updated', {
                'queue_item_id': self.queue_item_id,
                'project_id': None  # Will be populated by caller if needed
            })
        except:
            pass


def get_job_timeline(queue_item_id: int) -> list:
    """Get timeline for a specific job"""
    tracker = JobTracker(queue_item_id)
    return tracker.get_timeline()


def get_job_steps(queue_item_id: int) -> list:
    """Get all steps for a job"""
    tracker = JobTracker(queue_item_id)
    return tracker.get_steps()
